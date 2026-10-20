# ruff: noqa: RUF002, E501
"""Métricas del backtest, en MW e idénticas a las de Predico (PLAN F4.3).

Notación: `y` es la generación real (MW), `l = Q10`, `m = Q50` y `u = Q90` los pronósticos (MW),
`n` el número de cuartos de hora evaluados y `α = 0,2` (intervalo central del 80 %).

| Métrica | Fórmula |
|---|---|
| RMSE del Q50 | `sqrt( mean( (m − y)² ) )` |
| MAE | `mean( |m − y| )` |
| Sesgo | `mean( m − y )` (positivo = el modelo sobreestima) |
| Winkler (por punto) | `(u − l) + (2/α)·(l − y)·[y < l] + (2/α)·(y − u)·[y > u]` |
| Winkler medio | media del anterior; menor es mejor. Con α = 0,2: el exceso se multiplica por 10 |
| Cobertura | `mean( [l ≤ y ≤ u] )`; objetivo 0,80 (G1: entre 0,76 y 0,84) |
| Ancho medio | `mean( u − l )` |
| Error de pico | por día completo: `max(m) − max(y)` (MW) y desfase horario entre el cuarto de hora del `max(m)` y el del `max(y)` (h) |
| Ranking diario | % de días completos en que `RMSE_día(modelo) < RMSE_día(operador)` |

Si el modelo no da cuantiles (p. ej. el operador) las métricas de intervalo (Winkler, cobertura,
ancho) son `None`: nunca se inventan.
"""

import math
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

ALPHA = 0.2
Array = npt.NDArray[np.float64]
QUARTER_HOURS_PER_HOUR = 4.0

# Columnas que espera `summarize` (ver `evaluation/validate.py`).
EVAL_COLUMNS = (
    "target_date",
    "ts_utc",
    "n_steps_day",
    "y_mw",
    "q10_mw",
    "q50_mw",
    "q90_mw",
    "op_q50_mw",
)


def rmse(y: Array, m: Array) -> float:
    """`sqrt(mean((m − y)²))`."""
    return float(np.sqrt(np.mean((m - y) ** 2)))


def mae(y: Array, m: Array) -> float:
    """`mean(|m − y|)`."""
    return float(np.mean(np.abs(m - y)))


def bias(y: Array, m: Array) -> float:
    """`mean(m − y)`; positivo = sobreestimación."""
    return float(np.mean(m - y))


def winkler_scores(y: Array, lo: Array, hi: Array, alpha: float = ALPHA) -> Array:
    """Puntuación de Winkler por punto: ancho + (2/α)·exceso por debajo de `lo` o encima de `hi`."""
    below = np.maximum(lo - y, 0.0)
    above = np.maximum(y - hi, 0.0)
    return (hi - lo) + (2.0 / alpha) * (below + above)


def mean_winkler(y: Array, lo: Array, hi: Array, alpha: float = ALPHA) -> float:
    """Winkler medio (menor es mejor)."""
    return float(np.mean(winkler_scores(y, lo, hi, alpha)))


def coverage(y: Array, lo: Array, hi: Array) -> float:
    """Fracción de observaciones con `lo ≤ y ≤ hi`."""
    return float(np.mean((y >= lo) & (y <= hi)))


def mean_width(lo: Array, hi: Array) -> float:
    """Ancho medio `mean(hi − lo)`."""
    return float(np.mean(hi - lo))


def _num(x: Any) -> float | None:
    """Float de Python o `None` si es nulo o no finito (el JSON no admite NaN)."""
    if x is None:
        return None
    f = float(x)
    return f if math.isfinite(f) else None


def _daily(df: pl.DataFrame) -> pl.DataFrame:
    """Una fila por día completo con RMSE diario, picos y desfase horario."""
    d = (
        df.sort("ts_utc")
        .group_by("target_date", maintain_order=True)
        .agg(
            pl.len().alias("n"),
            pl.col("n_steps_day").first().alias("n_day"),
            ((pl.col("q50_mw") - pl.col("y_mw")) ** 2).mean().sqrt().alias("rmse_day"),
            ((pl.col("op_q50_mw") - pl.col("y_mw")) ** 2).mean().sqrt().alias("rmse_op_day"),
            pl.col("op_q50_mw").null_count().alias("op_nulls"),
            pl.col("y_mw").max().alias("peak_y"),
            pl.col("q50_mw").max().alias("peak_m"),
            pl.col("ts_utc").get(pl.col("y_mw").arg_max()).alias("t_y"),
            pl.col("ts_utc").get(pl.col("q50_mw").arg_max()).alias("t_m"),
        )
        .filter(pl.col("n") == pl.col("n_day"))
    )
    return d.with_columns(
        (pl.col("peak_m") - pl.col("peak_y")).alias("peak_err"),
        ((pl.col("t_m") - pl.col("t_y")).dt.total_minutes() / 60.0).alias("peak_shift_h"),
    )


def summarize(df: pl.DataFrame, *, rank_vs_operator: bool = True) -> dict[str, Any]:
    """Todas las métricas de un conjunto de filas (un pliegue, un mes o el total).

    `df` debe traer `EVAL_COLUMNS`, sin nulos en `y_mw` ni `q50_mw`. `q10_mw`/`q90_mw` nulos
    (todos) significan «sin intervalo». `op_q50_mw` es el pronóstico del operador en MW (puede
    traer nulos). Con `rank_vs_operator=False` (el propio operador) el ranking queda en `None`.
    """
    n = df.height
    out: dict[str, Any] = {"n_rows": n}
    if n == 0:
        return out
    y = df["y_mw"].to_numpy()
    m = df["q50_mw"].to_numpy()
    out |= {"rmse": rmse(y, m), "mae": mae(y, m), "bias": bias(y, m)}
    has_interval = df["q10_mw"].null_count() == 0 and df["q90_mw"].null_count() == 0
    if has_interval:
        lo, hi = df["q10_mw"].to_numpy(), df["q90_mw"].to_numpy()
        out |= {
            "winkler": mean_winkler(y, lo, hi),
            "coverage": coverage(y, lo, hi),
            "mean_width": mean_width(lo, hi),
        }
    else:
        out |= {"winkler": None, "coverage": None, "mean_width": None}
    has_op = df["op_q50_mw"].null_count() == 0
    out["rmse_operator"] = rmse(y, df["op_q50_mw"].to_numpy()) if has_op else None
    days = _daily(df)
    out["n_days"] = days.height
    if days.height:
        out |= {
            "peak_abs_error_mw": _num(days["peak_err"].abs().mean()),
            "peak_bias_mw": _num(days["peak_err"].mean()),
            "peak_shift_h": _num(days["peak_shift_h"].abs().mean()),
        }
    else:
        out |= {"peak_abs_error_mw": None, "peak_bias_mw": None, "peak_shift_h": None}
    ranked = days.filter(pl.col("op_nulls") == 0)
    if rank_vs_operator and has_op and ranked.height:
        wins = ranked.filter(pl.col("rmse_day") < pl.col("rmse_op_day")).height
        out |= {"days_better_than_operator_pct": 100.0 * wins / ranked.height}
    else:
        out["days_better_than_operator_pct"] = None
    out["n_days_ranked"] = ranked.height if rank_vs_operator and has_op else 0
    return {k: (_num(v) if isinstance(v, float) else v) for k, v in out.items()}
