# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false
# ruff: noqa: PLR2004
"""Validación de la plata de ENTSO-E con Pandera e informe JSON (`entsoe_<dataset>.json`).

El informe recoge cobertura, huecos (rango y longitud), cambios de resolución y revisiones
(mismo valor visto con distinto contenido en descargas distintas del bronce). Solo lee.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from predico50hz.ingest.entsoe.datasets import WIND_OFFSHORE, WIND_ONSHORE
from predico50hz.ingest.entsoe.silver import KEYS, load_points, silver_path

STEP = dt.timedelta(minutes=15)
MAX_LISTED = 25
# Cotas físicas holgadas (MW) de 50Hertz: marina ~1,8 GW y terrestre ~22 GW en 2025.
MAX_MW = {"offshore": 8_000.0, "onshore": 40_000.0}
PRICE_RANGE = (-1_000.0, 5_000.0)  # EUR/MWh; SDAC admite negativos (mín. armonizado -500)
CAPACITY_SLACK = 1.10  # generación > 110 % de la capacidad anual se reporta
UTC = pl.Datetime("us", "UTC")


def _chk(name: str, fn: Any) -> pa.Check:
    return pa.Check(fn, name=name, error=name)


def _between(col: str, lo: float, hi: float) -> pa.Check:
    return _chk("range", lambda d: d.lazyframe.select(pl.col(col).is_between(lo, hi)))


def schema(dataset: str) -> pa.DataFrameSchema:
    unique = _chk("unique_key", lambda d: d.lazyframe.select(pl.col("ts_utc").is_unique()))
    if dataset == "generation":
        cols: dict[str, pa.Column] = {"ts_utc": pa.Column(UTC)}
        for n in ("offshore", "onshore"):
            cols[f"wind_{n}_mw"] = pa.Column(
                pl.Float64, _between(f"wind_{n}_mw", 0.0, MAX_MW[n]), nullable=True
            )
            cols[f"{n}_src"] = pa.Column(
                pl.String, pa.Check.isin(["original", "a03", "hourly", "gap"]), nullable=True
            )
            cols[f"{n}_gap_len"] = pa.Column(pl.Int32, pa.Check.ge(0))
        cols["wind_mw"] = pa.Column(pl.Float64, pa.Check.ge(0.0), nullable=True)
        cols["is_gap_filled"] = pa.Column(pl.Boolean, nullable=True)
        grid = _chk(
            "full_grid",  # malla de 15 min sin huecos tras rellenar
            lambda d: d.lazyframe.select(
                pl.col("ts_utc").diff().drop_nulls().eq(pl.lit(STEP)).all()
            ),
        )
        return pa.DataFrameSchema(cols, checks=[unique, grid], strict=True)
    if dataset == "prices":
        return pa.DataFrameSchema(
            {
                "ts_utc": pa.Column(UTC),
                "price_eur_mwh": pa.Column(pl.Float64, _between("price_eur_mwh", *PRICE_RANGE)),
                "resolution_min": pa.Column(pl.Int32, pa.Check.isin([15, 60])),
                "fetched_at": pa.Column(UTC),
                "first_seen_at": pa.Column(UTC),
            },
            checks=[unique],
            strict=True,
        )
    if dataset == "forecast":
        return pa.DataFrameSchema(
            {
                "ts_utc": pa.Column(UTC),
                "psr_type": pa.Column(pl.String, pa.Check.isin([WIND_OFFSHORE, WIND_ONSHORE])),
                "forecast_mw": pa.Column(pl.Float64, pa.Check.between(0.0, 48_000.0)),
                "resolution_min": pa.Column(pl.Int32, pa.Check.isin([15, 60])),
                "doc_created": pa.Column(UTC),
                "doc_revision": pa.Column(pl.Int32, pa.Check.ge(1)),
                "fetched_at": pa.Column(UTC),
                "first_seen_at": pa.Column(UTC),
            },
            checks=[
                _chk(
                    "unique_key",
                    lambda d: d.lazyframe.select(
                        pl.struct("ts_utc", "psr_type").n_unique() == pl.len()
                    ),
                )
            ],
            strict=True,
        )
    return pa.DataFrameSchema(  # capacity
        {
            "year": pa.Column(pl.Int32),
            "psr_type": pa.Column(pl.String, pa.Check.isin([WIND_OFFSHORE, WIND_ONSHORE])),
            "installed_mw": pa.Column(pl.Float64, pa.Check.between(0.0, 40_000.0)),
            "area": pa.Column(pl.String),
            "fetched_at": pa.Column(UTC),
        },
        checks=[
            _chk(
                "unique_key",
                lambda d: d.lazyframe.select(pl.struct("year", "psr_type").n_unique() == pl.len()),
            )
        ],
        strict=True,
    )


def schema_issues(dataset: str, df: pl.DataFrame) -> list[dict[str, Any]]:
    try:
        schema(dataset).validate(df, lazy=True)
    except SchemaErrors as e:
        fc = e.failure_cases
        if isinstance(fc, pl.DataFrame) and not fc.is_empty():
            g = fc.group_by(["check", "column"], maintain_order=True).len()
            return [{"check": str(c), "column": col, "n": int(n)} for c, col, n in g.iter_rows()]
        return [{"check": "desconocido", "column": None, "n": 1}]
    except pl.exceptions.PolarsError as e:
        return [{"check": "polars", "column": None, "n": 1, "detail": str(e)[:200]}]
    return []


# --------------------------------------------------------------------------------------
# Componentes del informe
# --------------------------------------------------------------------------------------
def _iso(t: Any) -> str | None:
    return None if t is None else t.isoformat()


def gap_runs(df: pl.DataFrame, series: str) -> list[dict[str, Any]]:
    """Rachas de relleno de una serie de la plata de generación (rango y longitud en pasos)."""
    g = df.filter(pl.col(f"{series}_src") == "gap").select("ts_utc")
    if g.is_empty():
        return []
    runs = (
        g.with_columns(run=(pl.col("ts_utc").diff() != STEP).fill_null(True).cum_sum())
        .group_by("run", maintain_order=True)
        .agg(start=pl.col("ts_utc").min(), end=pl.col("ts_utc").max(), steps=pl.len())
    )
    return [
        {"series": series, "start": _iso(s), "end": _iso(e), "steps": n, "hours": n / 4}
        for _, s, e, n in runs.iter_rows()
    ]


def resolution_segments(points: pl.DataFrame) -> list[dict[str, Any]]:
    """Tramos mensuales con la resolución dominante de cada serie; marca los cambios."""
    if points.is_empty():
        return []
    pts = points.filter(pl.col("resolution_min").is_not_null())
    if pts.is_empty():
        return []
    by_month = (
        pts.with_columns(month=pl.col("ts_utc").dt.truncate("1mo"))
        .group_by(["psr_type", "month", "resolution_min"])
        .len()
        .sort("len", descending=True)
        .unique(["psr_type", "month"], keep="first")
        .sort(["psr_type", "month"])
    )
    out: list[dict[str, Any]] = []
    for psr in by_month.get_column("psr_type").unique(maintain_order=True):
        cur: dict[str, Any] | None = None
        for month, res in (
            by_month.filter(pl.col("psr_type").eq_missing(psr))
            .select("month", "resolution_min")
            .iter_rows()
        ):
            if cur and cur["resolution_min"] == res:
                cur["to"] = _iso(month)
            else:
                cur = {
                    "psr_type": psr,
                    "from": _iso(month),
                    "to": _iso(month),
                    "resolution_min": res,
                }
                out.append(cur)
    return out


def revisions(points: pl.DataFrame, dataset: str) -> dict[str, Any]:
    """Claves vistas en ≥ 2 descargas y cuántas cambiaron de valor entre ellas."""
    n_fetches = points.get_column("fetched_at").n_unique() if not points.is_empty() else 0
    if points.is_empty():
        return {"fetches": 0, "keys_compared": 0, "keys_revised": 0}
    key = [c for c in KEYS_RAW[dataset] if c in points.columns]
    per = points.group_by(key).agg(
        n=pl.col("fetched_at").n_unique(),
        nv=pl.col("value").n_unique(),
        spread=pl.col("value").max() - pl.col("value").min(),
    )
    compared = per.filter(pl.col("n") >= 2)
    revised = compared.filter(pl.col("nv") > 1)
    return {
        "fetches": n_fetches,
        "keys_compared": compared.height,
        "keys_revised": revised.height,
        "max_abs_change": float(revised["spread"].max()) if revised.height else 0.0,
        "examples": [
            {
                **dict(
                    zip(
                        key,
                        [_iso(v) if isinstance(v, dt.datetime) else v for v in r[: len(key)]],
                        strict=True,
                    )
                ),
                "spread": r[-1],
            }
            for r in revised.sort("spread", descending=True)
            .head(10)
            .select(*key, "spread")
            .iter_rows()
        ],
    }


KEYS_RAW = {
    "generation": ["psr_type", "ts_utc"],
    "capacity": ["psr_type", "ts_utc"],
    "prices": ["ts_utc"],
    "forecast": ["psr_type", "ts_utc"],
}


def _generation_report(df: pl.DataFrame, capacity: pl.DataFrame | None) -> dict[str, Any]:
    rep: dict[str, Any] = {"series": {}}
    all_runs: list[dict[str, Any]] = []
    for n in ("offshore", "onshore"):
        src = df.get_column(f"{n}_src")
        counts = {str(k): int(v) for k, v in src.value_counts().iter_rows() if k is not None}
        started = int(src.is_not_null().sum())
        runs = gap_runs(df, n)
        all_runs += runs
        first = df.filter(pl.col(f"{n}_src").is_not_null()).get_column("ts_utc").min()
        rep["series"][n] = {
            "first_value": _iso(first),
            "steps_by_source": counts,
            "coverage_original": counts.get("original", 0) / started if started else None,
            "gap_runs": len(runs),
            "gap_steps": sum(r["steps"] for r in runs),
            "gap_max_steps": max((r["steps"] for r in runs), default=0),
        }
    rep["longest_gaps"] = sorted(all_runs, key=lambda r: -r["steps"])[:MAX_LISTED]
    buckets = {"1": 0, "2-4 (<=1 h)": 0, "5-96 (<=1 d)": 0, ">96 (>1 d)": 0}
    for r in all_runs:
        s = r["steps"]
        buckets[
            "1"
            if s == 1
            else "2-4 (<=1 h)"
            if s <= 4
            else "5-96 (<=1 d)"
            if s <= 96
            else ">96 (>1 d)"
        ] += 1
    rep["gap_runs_by_length"] = buckets
    if capacity is not None and not capacity.is_empty():
        cap = capacity.pivot(on="psr_type", index="year", values="installed_mw")
        yearly = (
            df.with_columns(year=pl.col("ts_utc").dt.year().cast(pl.Int32))
            .group_by("year")
            .agg(
                offshore_max=pl.col("wind_offshore_mw").max(),
                onshore_max=pl.col("wind_onshore_mw").max(),
            )
            .sort("year")
            .join(cap, on="year", how="left")
        )
        rows: list[dict[str, Any]] = []
        for r in yearly.iter_rows(named=True):
            row: dict[str, Any] = {"year": r["year"]}
            for n, psr in (("offshore", WIND_OFFSHORE), ("onshore", WIND_ONSHORE)):
                c = r.get(psr)
                row[f"{n}_capacity_mw"] = c
                row[f"{n}_max_mw"] = r[f"{n}_max"]
                row[f"{n}_max_over_capacity"] = (
                    (r[f"{n}_max"] / c) if c and r[f"{n}_max"] is not None else None
                )
            rows.append(row)
        rep["max_vs_capacity_by_year"] = rows
        rep["exceeds_capacity"] = [
            r
            for r in rows
            if any(
                (r[f"{n}_max_over_capacity"] or 0) > CAPACITY_SLACK for n in ("offshore", "onshore")
            )
        ]
    return rep


def _forecast_report(df: pl.DataFrame) -> dict[str, Any]:
    lag = (df.get_column("fetched_at") - df.get_column("doc_created")).dt.total_seconds()
    return {
        "doc_created_min": _iso(df.get_column("doc_created").min()),
        "doc_created_max": _iso(df.get_column("doc_created").max()),
        "created_minus_fetched_s": {"min": float(lag.min()), "max": float(lag.max())},
        "doc_revision_counts": {
            str(k): int(v)
            for k, v in df.get_column("doc_revision")
            .value_counts()
            .sort("doc_revision")
            .iter_rows()
        },
        "first_seen_min": _iso(df.get_column("first_seen_at").min()),
        "first_seen_max": _iso(df.get_column("first_seen_at").max()),
        "note": "doc_created es la hora de generación de la respuesta, no de publicación",
    }


def _step_gaps(df: pl.DataFrame, by: str | None) -> list[dict[str, Any]]:
    """Saltos mayores que la resolución de la fila anterior (prices/forecast)."""
    frames = (
        [(None, df)] if by is None else [(k, g) for (k,), g in df.group_by(by, maintain_order=True)]
    )
    out: list[dict[str, Any]] = []
    for k, frame in frames:
        g = frame.sort("ts_utc").with_columns(
            nxt=pl.col("ts_utc").shift(-1), step=pl.duration(minutes=pl.col("resolution_min"))
        )
        miss = g.filter((pl.col("nxt") - pl.col("ts_utc")) > pl.col("step"))
        for t, nxt, step in miss.select("ts_utc", "nxt", "step").iter_rows():
            n = int((nxt - t) / step) - 1
            out.append({"series": k, "after": _iso(t), "until": _iso(nxt), "missing_steps": n})
    return out


def validate(dataset: str, bronze: Path, silver: Path, out_dir: Path) -> dict[str, Any]:
    path = silver_path(silver, dataset)
    df = pl.read_delta(str(path))
    points = load_points(bronze, dataset)
    rep: dict[str, Any] = {
        "dataset": dataset,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "rows": df.height,
        "keys": KEYS[dataset],
    }
    if "ts_utc" in df.columns:
        rep["first_ts"] = _iso(df.get_column("ts_utc").min())
        rep["last_ts"] = _iso(df.get_column("ts_utc").max())
    issues = schema_issues(dataset, df)
    rep["schema_ok"] = not issues
    rep["schema_issues"] = issues
    rep["resolution"] = resolution_segments(points)
    rep["revisions"] = revisions(points, dataset)
    if dataset == "generation":
        cap_path = silver_path(silver, "capacity")
        cap = pl.read_delta(str(cap_path)) if (cap_path / "_delta_log").exists() else None
        rep.update(_generation_report(df, cap))
    elif dataset in ("prices", "forecast"):
        gaps = _step_gaps(df, "psr_type" if dataset == "forecast" else None)
        rep["gaps"] = {
            "n": len(gaps),
            "missing_steps": sum(g["missing_steps"] for g in gaps),
            "longest": sorted(gaps, key=lambda g: -g["missing_steps"])[:MAX_LISTED],
        }
        if dataset == "forecast":
            rep.update(_forecast_report(df))
    else:
        rep["by_year"] = (
            df.sort(["year", "psr_type"])
            .select("year", "psr_type", "installed_mw", "area")
            .to_dicts()
        )
        rep["years_missing"] = sorted(
            set(range(int(df["year"].min()), int(df["year"].max()) + 1)) - set(df["year"].to_list())
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"entsoe_{dataset}.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    return rep
