# ruff: noqa: RUF002, RUF003
"""Calibración conformal de Q10 y Q90 (CQR, Romano et al. 2019): implementación propia.

**Método.** Con los residuos recientes se mide cuánto falló el intervalo y se ensancha (o se
estrecha) de forma simétrica en esa cantidad:

1. Puntuación de conformidad por cuarto de hora: `E = max(q10 − y, y − q90)`. Es positiva si la
   realidad cae fuera del intervalo (cuánto) y negativa si cae dentro (cuánto margen sobra).
2. Cuantil empírico con corrección de muestra finita: `Q̂` = el `ceil((n + 1)(1 − α))`-ésimo
   menor de los `n` residuos, es decir el cuantil de nivel `ceil((n + 1)(1 − α)) / n`, con
   `α = 0,2` (`evaluation.metrics.ALPHA`). Si ese índice supera `n` no hay garantía posible y
   no se calibra.
3. Ajuste simétrico: `[q10 − Q̂, q90 + Q̂]`. **`Q̂` nunca se recorta**: puede ser negativo
   (el intervalo se estrecha) y no hay `max(Q̂, 0)` (ADR 0006, ADR 0014).

**Escala.** Las puntuaciones se calculan en factor de capacidad (`E / capacidad`) y el ajuste se
multiplica por la capacidad de la fila que se calibra, para que no dependa de la potencia
instalada (que crece despacio).

**Ventana sin look-ahead (ADR 0009).** Para el cierre del día `D` (15:00 de Berlín, objetivo
`D+1`) solo se usan residuos cuya realidad ya estaba publicada antes de ese cierre. La realidad
del objetivo de un cierre `D'` (el día local `D'+1`) está completa cuando ese día termina, a las
00:00 de Berlín de `D'+2`, más el retraso de publicación de ENTSO-E (`publication_delay`, aún sin
medir, F2.5/F2.7; se toma un margen conservador de 6 h). Ese instante cae antes del cierre de `D`
(15:00 de `D`) para `D' = D − 2`, pero no para `D' = D − 1`: el objetivo de `D − 1` es el propio
día `D`, que a las 15:00 solo lleva medio día. Por tanto, **el último día objetivo utilizable es
`D − 1`** (cierre `D − 2`), y los objetivos `D` y `D+1` nunca entran. Dicho con el día que se
pronostica `T = D + 1`: solo cuentan los `target_date ≤ T − 2`. `last_usable_issue_date` lo
deduce con `features.timing` en lugar de aritmética a mano, y el retraso se puede subir sin
tocar nada más (con más de 15 h el último utilizable retrocede un día).

La ventana son los `window_days` (60) cierres consecutivos que terminan en el último utilizable.
Si tiene menos de `min_days` días con residuos, **no se ajusta**: `qhat_cf` queda nulo y
`calibrated = False` en la tabla de ajustes, y las predicciones de ese día no se tocan.

**Orden y límites.** Tras ajustar se fuerza `q10 ≤ q50 ≤ q90` y `0 ≤ q ≤ capacidad` igual que el
marco (`evaluation.backtest.finalize`, que el esquema Pandera de `evaluation/validate.py` exige):
`q10` baja hasta `q50` y `q90` sube hasta `q50` si un `Q̂` negativo los cruza, y luego se recorta
a `[0, capacidad]`.

**De dónde salen los residuos en el backtest** (decisión de diseño; ver `docs/f4-backtest.md`).
De las propias predicciones walk-forward ya hechas: cada día de validación se calibra con las
predicciones que el modelo del pliegue correspondiente hizo de días anteriores. Son fuera de
muestra, porque ningún pliegue entrena con su mes de validación. Los pliegues se encadenan, así
que los primeros días de un mes reutilizan los residuos del mes anterior, hechos por el modelo
del pliegue anterior (algo menos entrenado). No hace falta validación cruzada ni reentrenar, y
es lo que haría producción: residuos de las predicciones enviadas y la realidad publicada
después. Contrapartida: los primeros `min_days` días del primer pliegue no se calibran.
"""

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from predico50hz.evaluation.metrics import ALPHA, Array
from predico50hz.features.timing import closure_utc, target_window

WINDOW_DAYS = 60
MIN_DAYS = 14
PUBLICATION_DELAY = dt.timedelta(hours=6)
_ROUND = 9  # decimales para que `(n + 1)(1 − α)` no falle por coma flotante


@dataclass(frozen=True)
class CQRConfig:
    """Parámetros de la calibración."""

    window_days: int = WINDOW_DAYS
    min_days: int = MIN_DAYS
    publication_delay: dt.timedelta = PUBLICATION_DELAY
    alpha: float = ALPHA

    def as_dict(self) -> dict[str, Any]:
        """Parámetros serializables (JSON) para `run.json`."""
        return {
            "window_days": self.window_days,
            "min_days": self.min_days,
            "publication_delay_hours": self.publication_delay.total_seconds() / 3600.0,
            "alpha": self.alpha,
        }


def conformal_scores(q10: Array, q90: Array, y: Array) -> Array:
    """`E = max(q10 − y, y − q90)`: positivo fuera del intervalo, negativo dentro."""
    return np.maximum(q10 - y, y - q90)


def conformal_quantile(scores: Array, alpha: float = ALPHA) -> float | None:
    """`Q̂` de nivel `ceil((n + 1)(1 − α)) / n`; `None` si `n` es 0 o ese índice pasa de `n`."""
    n = len(scores)
    if n == 0:
        return None
    k = math.ceil(round((n + 1) * (1.0 - alpha), _ROUND))
    if k > n:
        return None
    return float(np.sort(scores)[k - 1])


def adjust_interval(q10: Array, q90: Array, qhat: float) -> tuple[Array, Array]:
    """Ajuste simétrico `[q10 − Q̂, q90 + Q̂]` (sin recortar `Q̂`)."""
    return q10 - qhat, q90 + qhat


def last_usable_issue_date(issue: dt.date, delay: dt.timedelta = PUBLICATION_DELAY) -> dt.date:
    """Último cierre `D'` cuya realidad estaba publicada antes del cierre de `issue`.

    La realidad de `D'` (día objetivo `D'+1`) está completa en `target_window(D')[1]` más el
    retraso de publicación. Con retrasos menores de 15 h sale `issue − 2`.
    """
    deadline = closure_utc(issue)
    d = issue
    while target_window(d)[1] + delay > deadline:
        d -= dt.timedelta(days=1)
    return d


def day_adjustments(preds: pl.DataFrame, cfg: CQRConfig | None = None) -> pl.DataFrame:
    """`Q̂` (en factor de capacidad) de cada cierre de `preds`, solo con residuos anteriores.

    `preds` trae las columnas del marco (`forecast_issue_date`, `y_mw`, `cap_mw`, `q10_mw`,
    `q90_mw`) con las predicciones **sin calibrar** de todos los pliegues, en MW. Devuelve una
    fila por cierre con `window_start`, `window_end` (cierres), `n_days`, `n_residuals`,
    `qhat_cf` (nulo si no se calibra) y `calibrated`. El `Q̂` de un cierre solo depende de filas
    de cierres ≤ `last_usable_issue_date`: ni de su propia realidad ni de la posterior.
    """
    cfg = cfg or CQRConfig()
    issues = preds["forecast_issue_date"].unique().sort().to_list()
    scores: dict[dt.date, Array] = {}
    for key, g in preds.group_by("forecast_issue_date"):
        day = key[0]
        assert isinstance(day, dt.date)
        e = conformal_scores(g["q10_mw"].to_numpy(), g["q90_mw"].to_numpy(), g["y_mw"].to_numpy())
        scores[day] = e / g["cap_mw"].to_numpy()
    rows: list[dict[str, Any]] = []
    one = dt.timedelta(days=1)
    for d in issues:
        last = last_usable_issue_date(d, cfg.publication_delay)
        first = last - (cfg.window_days - 1) * one
        used = sorted(s for s in scores if first <= s <= last)
        qhat: float | None = None
        if len(used) >= cfg.min_days:
            qhat = conformal_quantile(np.concatenate([scores[s] for s in used]), cfg.alpha)
        rows.append(
            {
                "forecast_issue_date": d,
                "window_start": first,
                "window_end": last,
                "n_days": len(used),
                "n_residuals": sum(len(scores[s]) for s in used),
                "qhat_cf": qhat,
                "calibrated": qhat is not None,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "forecast_issue_date": pl.Date,
            "window_start": pl.Date,
            "window_end": pl.Date,
            "n_days": pl.Int64,
            "n_residuals": pl.Int64,
            "qhat_cf": pl.Float64,
            "calibrated": pl.Boolean,
        },
    )


def calibrate_frame(
    preds: pl.DataFrame, cfg: CQRConfig | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Aplica la CQR a las predicciones del marco. Devuelve (predicciones, ajustes por día).

    Sustituye `q10_mw` y `q90_mw` por los intervalos calibrados (ordenados con `q50_mw` y
    recortados a `[0, cap_mw]`); los días sin `Q̂` se quedan como estaban. Añade `q10_raw_mw` y
    `q90_raw_mw` con los valores sin calibrar.
    """
    adj = day_adjustments(preds, cfg)
    out = (
        preds.join(
            adj.select("forecast_issue_date", "qhat_cf"),
            on="forecast_issue_date",
            how="left",
            maintain_order="left",
        )
        .with_columns(
            pl.col("q10_mw").alias("q10_raw_mw"),
            pl.col("q90_mw").alias("q90_raw_mw"),
            (pl.col("qhat_cf").fill_null(0.0) * pl.col("cap_mw")).alias("_shift"),
        )
        .with_columns(
            pl.min_horizontal(pl.col("q10_raw_mw") - pl.col("_shift"), pl.col("q50_mw")).alias(
                "_lo"
            ),
            pl.max_horizontal(pl.col("q90_raw_mw") + pl.col("_shift"), pl.col("q50_mw")).alias(
                "_hi"
            ),
        )
        .with_columns(
            pl.col("_lo").clip(lower_bound=0.0, upper_bound=pl.col("cap_mw")).alias("q10_mw"),
            pl.col("_hi").clip(lower_bound=0.0, upper_bound=pl.col("cap_mw")).alias("q90_mw"),
        )
        .drop("qhat_cf", "_shift", "_lo", "_hi")
    )
    return out, adj
