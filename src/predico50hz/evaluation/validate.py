# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false
"""Validación Pandera (backend Polars) de las predicciones de una ejecución de backtest.

Comprueba: clave (`forecast_issue_date`, `ts_utc`) única; malla completa (las claves son
exactamente las esperadas y cada día es contiguo en pasos de 15 min); sin nulos en Q50 ni en
la realidad; Q10 y Q90 nulos o presentes a la vez; Q10 ≤ Q50 ≤ Q90; 0 ≤ q ≤ capacidad.
"""

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

DT_UTC = pl.Datetime("us", "UTC")
EPS = 1e-6
STEP_MIN = 15
MAX_LISTED = 10


class PredictionsInvalidError(ValueError):
    """Las predicciones no cumplen el contrato; el mensaje lista los problemas."""


def _chk(name: str, fn) -> pa.Check:
    return pa.Check(fn, name=name, error=name)


def _interval_ok(data) -> pl.LazyFrame:
    lo, mid, hi = pl.col("q10_mw"), pl.col("q50_mw"), pl.col("q90_mw")
    return data.lazyframe.select(
        (lo.is_null() | ((lo <= mid + EPS) & (mid <= hi + EPS))).alias("ok")
    )


def _bounds_ok(data) -> pl.LazyFrame:
    cap = pl.col("cap_mw")
    cols = [pl.col(c) for c in ("q10_mw", "q50_mw", "q90_mw")]
    return data.lazyframe.select(
        pl.all_horizontal(*[c.is_null() | ((c >= -EPS) & (c <= cap + EPS)) for c in cols]).alias(
            "ok"
        )
    )


def _finite_ok(data) -> pl.LazyFrame:
    cols = ("y_mw", "q10_mw", "q50_mw", "q90_mw")
    return data.lazyframe.select(
        pl.all_horizontal(*[~pl.col(c).is_nan().fill_null(False) for c in cols]).alias("ok")
    )


def _pair_ok(data) -> pl.LazyFrame:
    return data.lazyframe.select(
        (pl.col("q10_mw").is_null() == pl.col("q90_mw").is_null()).alias("ok")
    )


SCHEMA = pa.DataFrameSchema(
    {
        "forecast_issue_date": pa.Column(pl.Date, nullable=False),
        "target_date": pa.Column(pl.Date, nullable=False),
        "ts_utc": pa.Column(DT_UTC, nullable=False),
        "fold": pa.Column(pl.Int64, nullable=False),
        "n_steps_day": pa.Column(pl.Int64, nullable=False),
        "y_mw": pa.Column(pl.Float64, nullable=False),
        "cap_mw": pa.Column(pl.Float64, nullable=False, checks=pa.Check.ge(0)),
        "q10_mw": pa.Column(pl.Float64, nullable=True),
        "q50_mw": pa.Column(pl.Float64, nullable=False),
        "q90_mw": pa.Column(pl.Float64, nullable=True),
        "op_q50_mw": pa.Column(pl.Float64, nullable=True),
    },
    checks=[
        _chk("q10_mw y q90_mw: ambos nulos o ambos presentes", _pair_ok),
        _chk("sin NaN en y ni en cuantiles", _finite_ok),
        _chk("q10 ≤ q50 ≤ q90", _interval_ok),
        _chk("0 ≤ q ≤ capacidad", _bounds_ok),
    ],
    unique=["forecast_issue_date", "ts_utc"],
    strict=False,
)


def _grid_problems(df: pl.DataFrame, expected: pl.DataFrame | None) -> list[str]:
    out: list[str] = []
    if expected is not None:
        keys = ["forecast_issue_date", "ts_utc"]
        missing = expected.select(keys).join(df.select(keys), on=keys, how="anti").height
        extra = df.select(keys).join(expected.select(keys), on=keys, how="anti").height
        if missing or extra:
            out.append(f"malla: faltan {missing} filas esperadas y sobran {extra}")
    gaps = (
        df.sort("forecast_issue_date", "ts_utc")
        .with_columns(pl.col("ts_utc").diff().over("forecast_issue_date").alias("d"))
        .filter(pl.col("d").is_not_null() & (pl.col("d").dt.total_minutes() != STEP_MIN))
        .height
    )
    if gaps:
        out.append(f"malla: {gaps} saltos distintos de {STEP_MIN} min dentro de un día")
    return out


def validate_predictions(df: pl.DataFrame, expected: pl.DataFrame | None = None) -> None:
    """Lanza `PredictionsInvalidError` con la lista de problemas; no devuelve nada si todo va bien.

    `expected` son las claves que deben estar (la malla de evaluación); si se omite solo se
    comprueba la contigüidad por día.
    """
    problems: list[str] = []
    try:
        SCHEMA.validate(df, lazy=True)
    except SchemaErrors as e:
        fc = e.failure_cases
        problems += sorted({str(r["check"]) for r in fc.iter_rows(named=True)})[:MAX_LISTED]
    except pa.errors.SchemaError as e:  # pragma: no cover - según versión de pandera
        problems.append(str(e)[:300])
    problems += _grid_problems(df, expected)
    if problems:
        raise PredictionsInvalidError("; ".join(problems))
