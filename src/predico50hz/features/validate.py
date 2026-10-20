# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false
"""Validación de la tabla oro con Pandera (backend Polars) e informe JSON `gold_features.json`.

Comprueba tipos, clave única, malla contigua de 15 min y nº de pasos (92/96/100) por día,
rangos físicos de cada familia de variables, ausencia de NaN (salvo racha con `gust_missing`),
`y_cf*` en [0, 1,1], nulos de objetivo solo donde falta la generación y
`available_at_* < closure_utc`. Solo lee.
"""

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from predico50hz.features import timing
from predico50hz.features.capacity import Cluster
from predico50hz.features.config import SPLIT_UNUSED, SPLITS, STEP
from predico50hz.features.registry import (
    BLOCKS,
    Column,
    available_col,
    build_registry,
    is_gust,
)

DT_UTC = pl.Datetime("us", "UTC")
MAX_LISTED = 25
CF_MAX = 1.1
MAX_MW = 40_000.0
# (patrón, mín, máx) por familia de variables; gana el primero que coincide. Márgenes holgados.
RANGES: list[tuple[str, float, float]] = [
    (r"ws100_.*_std$", 0.0, 15.0),
    (r"ws100_sea_minus_land$", -50.0, 50.0),
    (r"(ws100|wshub|ws10)_", 0.0, 70.0),
    (r"v3(hub)?_", 0.0, 3.0e5),
    (r"shear_", 0.0, 0.5),
    (r"rho_", 0.9, 1.5),
    (r"pdens_", 0.0, 4.5e5),
    (r"stab_", -30.0, 30.0),
    (r"t2m_", -40.0, 45.0),
    (r"gustfac_", 0.0, 10.0),
    (r"gust_missing$", 0.0, 1.0),
    (r"gust_", 0.0, 80.0),
    (r"wdir100_", -1.0 - 1e-9, 1.0 + 1e-9),
    (r"(u100|v100)_", -70.0, 70.0),
    (r"disp_land_std$", 0.0, 20.0),
    (r"disp_land_range$", 0.0, 50.0),
    (r"disp_land_cv$", 0.0, 3.0),
    (r"lead_hours$", 18.0, 54.0),
    (r"cap_sea_share$", 0.0, 1.0),
    (r"cap_.*_mw$", 0.0, MAX_MW),
    (r"hour_local$", 0.0, 24.0),
    (r"(hour|doy)_(sin|cos)$", -1.0 - 1e-9, 1.0 + 1e-9),
    (r"dow$", 0.0, 6.0),
    (r"month$", 1.0, 12.0),
    (r"(is_weekend|is_dst)$", 0.0, 1.0),
    (r"step_of_day$", 0.0, 99.0),
]
TARGET_RANGES = {
    "y_cf": (0.0, CF_MAX),
    "y_cf_land": (0.0, CF_MAX),
    "y_cf_sea": (0.0, CF_MAX),
}


def feature_range(name: str) -> tuple[float, float]:
    """Rango físico admitido de una columna feature (familia por prefijo)."""
    for pat, lo, hi in RANGES:
        if re.match(pat, name):
            return lo, hi
    raise KeyError(f"sin rango físico para {name}")


def _chk(name: str, fn: Any) -> pa.Check:
    return pa.Check(fn, name=name, error=name)


def _feature_column(name: str, dtype: Any) -> pa.Column:
    lo, hi = feature_range(name)
    if is_gust(name):
        ok = lambda d: d.lazyframe.select(  # noqa: E731
            (pl.col(name).is_nan() & (pl.col("gust_missing") == 1))
            | (pl.col(name).is_between(lo, hi) & (pl.col("gust_missing") == 0))
        )
        return pa.Column(dtype, _chk("range_or_missing_gust", ok), nullable=True)
    return pa.Column(dtype, pa.Check.in_range(lo, hi, include_min=True, include_max=True))


def _target_column(name: str, dtype: Any) -> pa.Column:
    if dtype == pl.Boolean:
        return pa.Column(dtype, nullable=True)
    lo, hi = TARGET_RANGES.get(name, (0.0, MAX_MW))
    ok = lambda d: d.lazyframe.select(  # noqa: E731
        pl.col(name).is_null() | pl.col(name).is_between(lo, hi)
    )
    return pa.Column(dtype, _chk("range", ok), nullable=True)


def schema(reg: list[Column]) -> pa.DataFrameSchema:
    """Esquema Pandera de la tabla oro derivado del registro de columnas."""
    meta_types: dict[str, Any] = {
        "forecast_issue_date": pl.Date,
        "target_date": pl.Date,
        "n_steps_day": pl.Int16,
        "capacity_ffilled": pl.Boolean,
        "split_v01": pl.String,
    }
    int_features = {"gust_missing", "dow", "month", "is_weekend", "is_dst"}
    cols: dict[str, pa.Column] = {}
    for c in reg:
        if c.role == "meta":
            cols[c.name] = pa.Column(meta_types.get(c.name, DT_UTC))
        elif c.role == "feature":
            dtype = pl.Int16 if c.name == "step_of_day" else pl.Float64
            dtype = pl.Int8 if c.name in int_features else dtype
            cols[c.name] = _feature_column(c.name, dtype)
        else:
            dtype = pl.Boolean if c.name == "y_is_gap_filled" else pl.Float64
            cols[c.name] = _target_column(c.name, dtype)
    cols["split_v01"] = pa.Column(pl.String, pa.Check.isin([s[0] for s in SPLITS] + [SPLIT_UNUSED]))
    key = ["forecast_issue_date", "ts_utc"]
    checks = [
        _chk("unique_key", lambda d: d.lazyframe.select(pl.struct(key).n_unique() == pl.len())),
        _chk(
            "contiguous_15min_grid",
            lambda d: d.lazyframe.select(
                pl.col("ts_utc")
                .diff()
                .over("forecast_issue_date", order_by="ts_utc")
                .fill_null(STEP)
                .eq(STEP)
                .all()
            ),
        ),
        _chk(
            "n_steps_day_matches_rows",
            lambda d: d.lazyframe.select(
                (pl.col("n_steps_day") == pl.len().over("forecast_issue_date")).all()
            ),
        ),
        _chk(
            "available_before_closure",
            lambda d: d.lazyframe.select(
                pl.all_horizontal(
                    *(pl.col(available_col(b)) < pl.col("closure_utc") for b in BLOCKS)
                ).all()
            ),
        ),
        _chk(
            "nwp_init_is_issue_day_00z",
            lambda d: d.lazyframe.select(
                (
                    pl.col("forecast_issue_date")
                    .cast(pl.Datetime("us"))
                    .dt.replace_time_zone("UTC")
                    == pl.col("nwp_init_time")
                ).all()
            ),
        ),
        _chk(
            "target_date_is_next_day",
            lambda d: d.lazyframe.select(
                (pl.col("target_date") == pl.col("forecast_issue_date") + pl.duration(days=1)).all()
            ),
        ),
        _chk(
            "cap_targets_not_null_and_cf_null_with_gen",
            lambda d: d.lazyframe.select(
                (
                    pl.col("y_cap_mw").is_not_null()
                    & (pl.col("y_gen_mw").is_null() == pl.col("y_cf").is_null())
                    & (pl.col("y_gen_land_mw").is_null() == pl.col("y_cf_land").is_null())
                    & (pl.col("y_gen_sea_mw").is_null() == pl.col("y_cf_sea").is_null())
                ).all()
            ),
        ),
    ]
    return pa.DataFrameSchema(cols, checks=checks, strict=True)


def schema_issues(reg: list[Column], df: pl.DataFrame) -> list[dict[str, Any]]:
    """Incumplimientos del esquema agrupados por (comprobación, columna); vacío si todo pasa."""
    try:
        schema(reg).validate(df, lazy=True)
    except SchemaErrors as e:
        fc = e.failure_cases
        if isinstance(fc, pl.DataFrame) and not fc.is_empty():
            g = fc.group_by(["check", "column"], maintain_order=True).len()
            return [{"check": str(c), "column": col, "n": int(n)} for c, col, n in g.iter_rows()]
        return [{"check": "desconocido", "column": None, "n": 1}]
    except pl.exceptions.PolarsError as e:
        return [{"check": "polars", "column": None, "n": 1, "detail": str(e)[:200]}]
    return []


def day_issues(df: pl.DataFrame) -> list[dict[str, Any]]:
    """Cada día tiene exactamente la ventana UTC y los pasos (92/96/100) que marca Berlín."""
    issues: list[dict[str, Any]] = []
    g = df.group_by("forecast_issue_date").agg(
        n=pl.len(), first=pl.col("ts_utc").min(), last=pl.col("ts_utc").max()
    )
    for d, n, first, last in g.sort("forecast_issue_date").iter_rows():
        grid = timing.target_grid(d)
        if n != len(grid) or first != grid[0] or last != grid[-1]:
            issues.append(
                {"check": "day_window", "date": d.isoformat(), "rows": n, "expected": len(grid)}
            )
    return issues[:MAX_LISTED]


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, dt.date | dt.datetime) else v


def build_report(df: pl.DataFrame, reg: list[Column]) -> dict[str, Any]:
    """Informe de la tabla oro: tamaño, pasos por día, NaN, rangos y objetivo incompleto."""
    feats = [c.name for c in reg if c.role == "feature"]
    nan_cols: dict[str, int] = {}
    for c in feats:
        s = df.get_column(c)
        n = int(s.is_null().sum())
        if s.dtype.is_float():
            n += int(s.is_nan().sum())
        if n:
            nan_cols[c] = n
    obs = df.select(
        *(pl.col(c).filter(pl.col(c).is_not_nan()).min().alias(f"{c}|min") for c in feats),
        *(pl.col(c).filter(pl.col(c).is_not_nan()).max().alias(f"{c}|max") for c in feats),
    ).row(0, named=True)
    ranges = {c: [obs[f"{c}|min"], obs[f"{c}|max"]] for c in feats}
    per_day = df.group_by("forecast_issue_date").agg(
        n=pl.len(), nulls=pl.col("y_gen_mw").is_null().sum()
    )
    steps = per_day.group_by("n").len().sort("n")
    incomplete = (
        per_day.filter(pl.col("nulls") > 0)
        .sort("forecast_issue_date")
        .select("forecast_issue_date", "nulls")
    )
    margin = df.select(
        (pl.col("closure_utc") - pl.col("available_at_nwp")).dt.total_minutes().min()
    ).item()
    by_split = (
        df.group_by("split_v01")
        .agg(
            days=pl.col("forecast_issue_date").n_unique(),
            rows=pl.len(),
            y_cf_mean=pl.col("y_cf").mean(),
        )
        .sort("split_v01")
    )
    return {
        "rows": df.height,
        "days": per_day.height,
        "first_issue_date": _iso(df.get_column("forecast_issue_date").min()),
        "last_issue_date": _iso(df.get_column("forecast_issue_date").max()),
        "days_by_steps": {str(n): int(c) for n, c in steps.iter_rows()},
        "columns": df.width,
        "features": len(feats),
        "nan_by_column": nan_cols,
        "feature_ranges": ranges,
        "incomplete_target_days": [
            {"issue_date": _iso(d), "null_rows": int(n)} for d, n in incomplete.iter_rows()
        ][:MAX_LISTED],
        "n_incomplete_target_days": incomplete.height,
        "min_margin_nwp_to_closure_min": margin,
        "by_split": [
            {"split": s, "days": int(d), "rows": int(r), "y_cf_mean": m}
            for s, d, r, m in by_split.iter_rows()
        ],
    }


def validate(gold: Path, out_dir: Path, clusters: list[Cluster]) -> dict[str, Any]:
    """Valida la tabla oro en `gold` y escribe `out_dir/gold_features.json`."""
    reg = build_registry(clusters)
    df = pl.read_delta(str(gold))
    issues = schema_issues(reg, df) + day_issues(df)
    rep: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "gold": str(gold),
        "schema_ok": not issues,
        "problems": issues,
        **build_report(df, reg),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gold_features.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    return rep
