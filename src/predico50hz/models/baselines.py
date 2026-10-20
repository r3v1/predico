"""Referencias mínimas del marco de backtest (parte de F5).

- `climatology`: cuantiles empíricos del factor de capacidad por hora local y mes.
- `power_curve`: cuantiles del factor de capacidad por tramos de `wshub_all_mean`, interpolados.
- `operator`: pronóstico day-ahead del operador (ENTSO-E B18 + B19) como referencia externa:
  solo Q50, en MW. Es SOLO benchmark; nunca entra en `predico50hz.features`.

**Persistencia: fuera, a propósito.** Exige saber hasta qué hora hay generación publicada antes
del cierre (15:00 de Berlín) y esa hora de publicación no está medida (PLAN F2.5/F2.7). Sin ella,
«lo de ayer» no se puede construir sin mirar al futuro o sin inventar un retraso.
"""

from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import polars as pl

from predico50hz.models.base import Array, Model, Prediction, Unit

DEFAULT_OPERATOR = "data/silver/entsoe/forecast"
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
