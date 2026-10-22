"""Referencias mínimas del marco de backtest (parte de F5).

- `climatology`: cuantiles empíricos del factor de capacidad por hora local y mes.
- `power_curve`: cuantiles del factor de capacidad por tramos de `wshub_all_mean`, interpolados.
- `operator`: pronóstico day-ahead del operador (ENTSO-E B18 + B19) como referencia externa:
  solo Q50, en MW. Es SOLO benchmark; nunca entra en `predico50hz.features`.

- `persistence`: repite lo último conocido de la generación real (ENTSO-E B18 + B19, en MW) en
  el cierre. Solo usa cuartos de hora que empiezan como muy tarde en `cierre - lag_min`
  (180 min por defecto: retraso normal de 30 a 62 min en octubre de 2026 y atasco de ENTSO-E
  del 22 oct de hasta 166 min, issue #11). Intervalo Q10/Q90 con los cuantiles empíricos del
  error por horizonte.
"""

import datetime as dt
from pathlib import Path
from typing import Any, ClassVar, Literal

import numpy as np
import polars as pl

from predico50hz.features.timing import closure_utc
from predico50hz.models.base import Array, Model, Prediction, Unit

DEFAULT_OPERATOR = "data/silver/entsoe/forecast"
DEFAULT_GENERATION = "data/silver/entsoe/generation"
STEP_S = 900  # malla de 15 min
STEPS_PER_DAY = 96
OPERATOR_PSR = ("B18", "B19")  # eólica marina y terrestre
Q_LO, Q_HI = 0.1, 0.9
MONTHS = 12


def load_operator_mw(path: str | Path = DEFAULT_OPERATOR) -> pl.DataFrame:
    """Pronóstico del operador en MW por `ts_utc` (B18 + B19). Nulo si falta alguno de los dos."""
    f = pl.read_delta(str(path)).filter(pl.col("psr_type").is_in(OPERATOR_PSR))
    return (
        f.group_by("ts_utc")
        .agg(pl.col("forecast_mw").sum().alias("op_mw"), pl.col("psr_type").n_unique().alias("n"))
        .filter(pl.col("n") == len(OPERATOR_PSR))
        .select("ts_utc", "op_mw")
    )


def _quantiles(x: Array, central: str) -> tuple[float, float, float]:
    q10, q90 = np.quantile(x, [Q_LO, Q_HI])
    mid = float(np.mean(x)) if central == "mean" else float(np.median(x))
    return float(q10), mid, float(q90)


class Climatology(Model):
    """Cuantiles del factor de capacidad por hora local y mes del año.

    Con una sola temporada de entrenamiento casi nunca hay datos del mismo mes natural en el
    pliegue previo, así que se usan los meses a distancia circular ≤ `month_window` del mes
    objetivo (1 = ese mes y sus dos vecinos) y, si no hay ninguno, todo el entrenamiento. El
    Q50 es la media (`central="mean"`), porque la puntuación es el RMSE.
    """

    name: ClassVar[str] = "climatology"
    is_reference: ClassVar[bool] = True
    label: ClassVar[str] = "Media histórica por hora y mes"

    def __init__(self, month_window: int = 1, central: str = "mean") -> None:
        self.month_window = month_window
        self.central = central
        self._month = np.empty(0, dtype=np.int64)
        self._hour = np.empty(0, dtype=np.int64)
        self._cf = np.empty(0, dtype=np.float64)

    def params(self) -> dict[str, Any]:
        return {"month_window": self.month_window, "central": self.central}

    def fit(self, train: pl.DataFrame) -> None:
        t = train.drop_nulls("y_cf")
        self._month = t["month"].to_numpy().astype(np.int64)
        self._hour = np.floor(t["hour_local"].to_numpy()).astype(np.int64)
        self._cf = t["y_cf"].to_numpy().astype(np.float64)

    def _table(self, month: int) -> dict[int, tuple[float, float, float]]:
        dist = np.abs(self._month - month)
        dist = np.minimum(dist, MONTHS - dist)
        sel = dist <= self.month_window
        if not sel.any():
            sel = np.ones_like(sel)
        table: dict[int, tuple[float, float, float]] = {}
        for h in range(24):
            m = sel & (self._hour == h)
            if not m.any():  # hora sin datos en la ventana de meses: toda la hora
                m = self._hour == h
            table[h] = _quantiles(self._cf[m] if m.any() else self._cf, self.central)
        return table

    def predict(self, df: pl.DataFrame) -> Prediction:
        months = df["month"].to_numpy().astype(np.int64)
        hours = np.floor(df["hour_local"].to_numpy()).astype(np.int64)
        out = np.full((len(df), 3), np.nan)
        for mo in np.unique(months):
            table = self._table(int(mo))
            rows = months == mo
            for h in np.unique(hours[rows]):
                out[rows & (hours == h)] = table[int(h)]
        return Prediction(q50=out[:, 1], q10=out[:, 0], q90=out[:, 2])


class PowerCurve(Model):
    """Curva de potencia empírica: cuantiles de `y_cf` por tramos de viento de buje previsto.

    Tramos de `bin_width` m/s sobre `wshub_all_mean`; los de menos de `min_count` filas se
    descartan y la interpolación lineal entre los centros (viento medio de cada tramo) los cubre.
    Fuera del rango entrenado se mantiene el valor del tramo extremo.
    """

    name: ClassVar[str] = "power_curve"
    is_reference: ClassVar[bool] = True
    label: ClassVar[str] = "Curva de potencia sobre el viento previsto"
    feature: ClassVar[str] = "wshub_all_mean"

    def __init__(self, bin_width: float = 0.5, min_count: int = 30, central: str = "mean") -> None:
        self.bin_width = bin_width
        self.min_count = min_count
        self.central = central
        self._x = np.empty(0, dtype=np.float64)
        self._q = np.empty((0, 3), dtype=np.float64)

    def params(self) -> dict[str, Any]:
        return {
            "bin_width": self.bin_width,
            "min_count": self.min_count,
            "central": self.central,
            "feature": self.feature,
        }

    def fit(self, train: pl.DataFrame) -> None:
        t = train.drop_nulls("y_cf")
        ws = t[self.feature].to_numpy().astype(np.float64)
        cf = t["y_cf"].to_numpy().astype(np.float64)
        idx = np.floor(ws / self.bin_width).astype(np.int64)
        xs: list[float] = []
        qs: list[tuple[float, float, float]] = []
        for b in np.unique(idx):
            m = idx == b
            if int(m.sum()) < self.min_count:
                continue
            xs.append(float(ws[m].mean()))
            qs.append(_quantiles(cf[m], self.central))
        if not xs:
            raise ValueError("ningún tramo de viento con filas suficientes")
        self._x = np.asarray(xs)
        self._q = np.asarray(qs)

    def predict(self, df: pl.DataFrame) -> Prediction:
        ws = df[self.feature].to_numpy().astype(np.float64)
        q = [np.interp(ws, self._x, self._q[:, k]) for k in range(3)]
        return Prediction(q50=q[1], q10=q[0], q90=q[2])


class Operator(Model):
    """Pronóstico day-ahead del operador (B18 + B19) en MW; sin cuantiles.

    No se entrena. Las filas sin pronóstico del operador salen como NaN y la validación del
    marco las rechaza (en la tabla actual no hay ninguna).
    """

    name: ClassVar[str] = "operator"
    is_reference: ClassVar[bool] = True
    label: ClassVar[str] = "Previsión del operador"
    unit: ClassVar[Unit] = "mw"
    is_operator: ClassVar[bool] = True

    def __init__(self, source: str | Path = DEFAULT_OPERATOR) -> None:
        self.source = str(source)
        self._op: pl.DataFrame | None = None

    def params(self) -> dict[str, Any]:
        return {"source": self.source, "psr_types": list(OPERATOR_PSR)}

    def fit(self, train: pl.DataFrame) -> None:
        if self._op is None:
            self._op = load_operator_mw(self.source)

    def predict(self, df: pl.DataFrame) -> Prediction:
        if self._op is None:
            self._op = load_operator_mw(self.source)
        joined = df.select("ts_utc").join(self._op, on="ts_utc", how="left", maintain_order="left")
        return Prediction(q50=joined["op_mw"].to_numpy().astype(np.float64))


def load_generation_mw(path: str | Path = DEFAULT_GENERATION) -> tuple[int, Array]:
    """Generación real B18 + B19 en MW sobre la malla de 15 min: (primer paso, valores).

    El paso es `ts_utc` en segundos de época // 900. Los huecos y los puntos con
    `is_gap_filled` quedan como NaN: el relleno interpola con valores posteriores al hueco y,
    cerca del corte, podría usar datos que aún no existían.
    """
    g = pl.read_delta(str(path)).select("ts_utc", "wind_mw", "is_gap_filled")
    g = g.filter(pl.col("wind_mw").is_not_null() & ~pl.col("is_gap_filled").fill_null(False))
    steps = g["ts_utc"].dt.epoch("s").to_numpy().astype(np.int64) // STEP_S
    vals = g["wind_mw"].to_numpy().astype(np.float64)
    first = int(steps.min())
    out = np.full(int(steps.max()) - first + 1, np.nan)
    out[steps - first] = vals
    return first, out


class Persistence(Model):
    """Repite lo último conocido de la generación real en el cierre, con margen de publicación.

    Para el cierre D el corte es `closure_utc(D) - lag_min` (hora de Berlín, con horario de
    verano e invierno): solo cuentan los cuartos de hora que empiezan en el corte o antes.
    - `mode="last"` (por defecto, el de menor RMSE en el backtest): el último valor conocido,
      plano para todo el día objetivo.
    - `mode="profile"`: cada cuarto objetivo repite el mismo cuarto del día conocido más
      reciente (`ts - 24 h·k`, k mínimo con ese instante ≤ corte; es decir, las últimas 24 h
      conocidas alineadas por hora UTC). Si ese punto falta, cae al último valor.
    Si no hay ningún valor en las `max_stale_min` anteriores al corte, la fila sale NaN y la
    validación del marco la rechaza (en la tabla real no ocurre).

    Unidad MW (el marco quita `y_cap_mw` antes de `predict`). Q50 = la persistencia pura; Q10 y
    Q90 = Q50 + cuantiles empíricos del error de entrenamiento por tramo de horizonte (horas
    desde el corte, `horizon_edges_h`); un tramo con menos de `min_count` filas usa todos. Sin
    `fit` solo se devuelve Q50.
    """

    name: ClassVar[str] = "persistence"
    is_reference: ClassVar[bool] = True
    label: ClassVar[str] = "Repetir lo último conocido"
    unit: ClassVar[Unit] = "mw"

    def __init__(  # noqa: PLR0913
        self,
        *,
        lag_min: int = 180,
        mode: Literal["profile", "last"] = "last",
        max_stale_min: int = 360,
        horizon_edges_h: tuple[float, ...] = (12.0, 18.0, 24.0, 30.0),
        min_count: int = 200,
        source: str | Path = DEFAULT_GENERATION,
    ) -> None:
        if mode not in ("profile", "last"):
            raise ValueError(f"modo desconocido {mode!r}")
        self.lag_min = lag_min
        self.mode = mode
        self.max_stale_min = max_stale_min
        self.horizon_edges_h = tuple(float(e) for e in horizon_edges_h)
        self.min_count = min_count
        self.source = str(source)
        self._series: tuple[int, Array] | None = None
        self._err: np.ndarray | None = None  # (tramos, 2): cuantiles Q10 y Q90 del error
        self._err_all = np.zeros(2)

    def params(self) -> dict[str, Any]:
        return {
            "lag_min": self.lag_min,
            "mode": self.mode,
            "max_stale_min": self.max_stale_min,
            "horizon_edges_h": list(self.horizon_edges_h),
            "min_count": self.min_count,
            "source": self.source,
            "gap_filled": "excluido",
        }

    def _gen(self) -> tuple[int, Array]:
        if self._series is None:
            self._series = load_generation_mw(self.source)
        return self._series

    def _cutoff_step(self, issue: pl.Series) -> np.ndarray:
        cut = {
            d: int((closure_utc(d) - dt.timedelta(minutes=self.lag_min)).timestamp() // STEP_S)
            for d in issue.unique().to_list()
        }
        return np.array([cut[d] for d in issue.to_list()], dtype=np.int64)

    def _persist(self, df: pl.DataFrame) -> tuple[Array, Array]:
        """(persistencia en MW, horas desde el corte) por fila; NaN si no hay dato utilizable."""
        first, vals = self._gen()
        n = len(vals)
        c = self._cutoff_step(df["forecast_issue_date"])
        t = df["ts_utc"].dt.epoch("s").to_numpy().astype(np.int64) // STEP_S
        valid = ~np.isnan(vals)
        last_idx = np.where(valid, np.arange(n), -1)
        last_idx = np.maximum.accumulate(last_idx)
        pos = np.clip(c - first, -1, n - 1)
        li = np.where(pos >= 0, last_idx[np.maximum(pos, 0)], -1)
        fresh = (li >= 0) & ((c - first - li) * STEP_S <= self.max_stale_min * 60)
        last = np.where(fresh, vals[np.maximum(li, 0)], np.nan)
        if self.mode == "last":
            out = last
        else:
            k = np.maximum(-(-(t - c) // STEPS_PER_DAY), 1)  # ceil((t - c) / 96)
            src = t - k * STEPS_PER_DAY - first
            ok = (src >= 0) & (src < n)
            prof = np.where(ok, vals[np.clip(src, 0, n - 1)], np.nan)
            out = np.where(np.isnan(prof), last, prof)
        return out, np.asarray((t - c) * (STEP_S / 3600), dtype=np.float64)

    def fit(self, train: pl.DataFrame) -> None:
        t = train.drop_nulls("y_gen_mw")
        pers, hours = self._persist(t)
        err = t["y_gen_mw"].to_numpy().astype(np.float64) - pers
        ok = ~np.isnan(err)
        err, hours = err[ok], hours[ok]
        if err.size == 0:
            raise ValueError("sin filas de entrenamiento con persistencia calculable")
        self._err_all = np.quantile(err, [Q_LO, Q_HI])
        bins = np.digitize(hours, self.horizon_edges_h)
        table = np.tile(self._err_all, (len(self.horizon_edges_h) + 1, 1))
        for b in range(len(table)):
            m = bins == b
            if int(m.sum()) >= self.min_count:
                table[b] = np.quantile(err[m], [Q_LO, Q_HI])
        self._err = table

    def predict(self, df: pl.DataFrame) -> Prediction:
        pers, hours = self._persist(df)
        if self._err is None:
            return Prediction(q50=pers)
        e = self._err[np.digitize(hours, self.horizon_edges_h)]
        return Prediction(q50=pers, q10=pers + e[:, 0], q90=pers + e[:, 1])
