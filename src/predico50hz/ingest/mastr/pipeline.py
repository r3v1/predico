# ruff: noqa: PLR0913, PLR0917
"""Orquestación: ZIP del export -> unidades -> clústeres -> capacidad -> plata, YAML e informe."""

import datetime as dt
import json
import zipfile
from pathlib import Path
from typing import Any

import polars as pl

from predico50hz.ingest.mastr import compare
from predico50hz.ingest.mastr.capacity import daily_capacity
from predico50hz.ingest.mastr.centroids import stamp, write_centroids
from predico50hz.ingest.mastr.clusterize import (
    GRID_EW_KM,
    GRID_NS_KM,
    K_RANGE,
    OPERATING,
    SEA_MERGE_KM,
    SEED,
    assign_clusters,
    in_domain,
    land_clusters,
    sea_clusters,
    weight_sensitivity,
)
from predico50hz.ingest.mastr.config import CAPACITY_START
from predico50hz.ingest.mastr.extract import read_catalog, read_grid_points, read_wind_raw
from predico50hz.ingest.mastr.silver import read_silver, silver_path, write_silver
from predico50hz.ingest.mastr.units import resolve_coordinates, typed_units
from predico50hz.ingest.mastr.validate import (
    capacity_schema,
    schema_issues,
    units_schema,
)

SHARED_COORD_MIN = 5
SENSITIVITY_DATES = [dt.date(2020, 1, 1), dt.date(2023, 1, 1)]


def build_units(zip_path: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Unidades tipadas con zona y coordenadas, más cifras de extracción para el informe."""
    with zipfile.ZipFile(zip_path) as zf:
        raw = read_wind_raw(zf)
        cat = read_catalog(zf)
    locs = {x for x in raw["LokationMaStRNummer"].to_list() if x}
    nap = read_grid_points(zip_path, locs)
    units = resolve_coordinates(typed_units(raw, cat, nap))
    status_raw = raw.with_columns(
        status=pl.col("EinheitBetriebsstatus").replace_strict(cat, default=None)
    )
    info = {
        "raw_units": raw.height,
        "raw_by_status": dict(status_raw.group_by("status").len().iter_rows()),
        "locations": len(locs),
        "locations_with_grid_point": nap["location"].n_unique(),
        "grid_points": nap.height,
    }
    return units, info


def build_capacity(
    units: pl.DataFrame, start: dt.date, end: dt.date
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(capacidad por clúster, capacidad total tierra/mar) diarias de la zona."""
    zone = units.filter(pl.col("in_zone"))
    by_cluster = daily_capacity(
        zone.filter(pl.col("cluster_id").is_not_null()), "cluster_id", start, end
    )
    total = daily_capacity(zone, "kind", start, end)
    return by_cluster, total


def _mw(df: pl.DataFrame) -> float:
    return float(df["net_mw"].sum())


def zone_report(units: pl.DataFrame) -> dict[str, Any]:
    """Regla de zona aplicada y casos dudosos, sobre las unidades activas (con su capacidad)."""
    act = units.filter(pl.col("status").is_in(OPERATING))
    zone = act.filter(pl.col("in_zone"))
    by_rule = (
        zone.group_by("kind", "zone_rule")
        .agg(n=pl.len(), mw=pl.col("net_mw").sum())
        .sort("kind", "zone_rule")
    )
    doubt = (
        act.filter(pl.col("zone_doubtful"))
        .group_by("bundesland", "regelzone", "in_zone")
        .agg(n=pl.len(), mw=pl.col("net_mw").sum())
        .sort("mw", descending=True)
    )
    # Qué cambiaría usando solo el Bundesland (regla "ingenua").
    naive_only = act.filter(pl.col("zone_by_state") & ~pl.col("in_zone"))
    zone_only = act.filter(~pl.col("zone_by_state") & pl.col("in_zone"))
    return {
        "active_in_zone": {
            k: {
                "n": zone.filter(pl.col("kind") == k).height,
                "mw": _mw(zone.filter(pl.col("kind") == k)),
            }
            for k in ("land", "sea")
        },
        "by_rule": by_rule.to_dicts(),
        "doubtful": doubt.to_dicts(),
        "bundesland_rule_only_would_add": {
            "n": naive_only.height,
            "mw": _mw(naive_only),
            "detail": naive_only.group_by("bundesland", "regelzone")
            .agg(n=pl.len(), mw=pl.col("net_mw").sum())
            .to_dicts(),
        },
        "bundesland_rule_only_would_miss": {
            "n": zone_only.height,
            "mw": _mw(zone_only),
            "detail": zone_only.group_by("bundesland", "regelzone")
            .agg(n=pl.len(), mw=pl.col("net_mw").sum())
            .to_dicts(),
        },
    }


def coords_report(units: pl.DataFrame) -> dict[str, Any]:
    act = units.filter(pl.col("in_zone") & pl.col("status").is_in(OPERATING))
    raw_ok = act.filter(pl.col("coord_source").is_in(["unit", "unit_swapped"]))
    missing = act.filter(~pl.col("coord_source").is_in(["unit", "unit_swapped"]).fill_null(False))
    shared = (
        raw_ok.group_by("lat_raw", "lon_raw")
        .agg(n=pl.len(), mw=pl.col("net_mw").sum())
        .filter(pl.col("n") >= SHARED_COORD_MIN)
    )
    return {
        "active_units": act.height,
        "active_mw": _mw(act),
        "raw_ok": {"n": raw_ok.height, "mw": _mw(raw_ok)},
        "swapped_axes": raw_ok.filter(pl.col("coord_source") == "unit_swapped").height,
        "missing_or_invalid": {"n": missing.height, "mw": _mw(missing)},
        "resolved_by": missing.group_by("coord_source")
        .agg(n=pl.len(), mw=pl.col("net_mw").sum())
        .sort("n", descending=True)
        .to_dicts(),
        "unresolved": act.filter(pl.col("lat").is_null()).height,
        "coords_shared_by_5plus_units": {"points": shared.height, "mw": float(shared["mw"].sum())},
    }


def run(
    zip_path: Path,
    export_date: str,
    silver: Path,
    validation: Path,
    centroids: Path,
    k: int | None = None,
    seed: int = SEED,
) -> dict[str, Any]:
    exp = dt.datetime.strptime(export_date, "%Y%m%d").date()
    units, extract_info = build_units(zip_path)
    land, res, ktab = land_clusters(units, k, seed)
    k_used = land.height
    sea = sea_clusters(units)
    clusters = pl.concat([land, sea.select(land.columns)])
    outside = in_domain(clusters)
    units = assign_clusters(units, clusters)
    by_cluster, total = build_capacity(units, dt.date.fromisoformat(CAPACITY_START), exp)
    clusters = clusters.with_columns(
        centroid_rmse_km=pl.when(pl.col("kind") == "land").then(res.rmse_km)
    )
    sens = weight_sensitivity(units, land, k_used, SENSITIVITY_DATES, seed)

    silver.mkdir(parents=True, exist_ok=True)
    written = {
        name: write_silver(df, silver_path(silver, name))
        for name, df in {
            "units": units.sort("unit_id"),
            "clusters": clusters.sort("cluster_id"),
            "capacity_cluster": by_cluster,
            "capacity_total": total,
        }.items()
    }
    write_centroids(clusters, stamp(export_date, k_used, seed), centroids)

    zone_units = units.filter(pl.col("in_zone"))
    issues = {
        "units": schema_issues(units_schema(exp), zone_units),
        "capacity_cluster": schema_issues(capacity_schema("cluster_id"), by_cluster),
        "capacity_total": schema_issues(capacity_schema("kind"), total),
    }
    now = total.filter(pl.col("date") == total["date"].max())
    cluster_now = by_cluster.filter(pl.col("date") == by_cluster["date"].max())
    rep: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": "MaStR Gesamtdatenexport (Bundesnetzagentur, DL-DE-BY-2.0)",
        "export_date": export_date,
        "zip": str(zip_path),
        "extraction": extract_info,
        "units_in_silver": units.height,
        "units_in_zone": zone_units.height,
        "schema_ok": not any(issues.values()),
        "schema_issues": issues,
        "silver_written": written,
        "zone": zone_report(units),
        "coordinates": coords_report(units),
        "clusters": {
            "k_land": k_used,
            "k_chosen_by": "criterio automático" if k is None else "fijado por --k",
            "n_sea": sea.height,
            "seed": seed,
            "k_range": list(K_RANGE),
            "grid_ns_km": GRID_NS_KM,
            "grid_ew_km": GRID_EW_KM,
            "sea_merge_km": SEA_MERGE_KM,
            "k_table": [r.__dict__ for r in ktab],
            "centroids_outside_domain": outside["cluster_id"].to_list(),
            "weight_sensitivity": sens,
            "table": clusters.drop("centroid_rmse_km").sort("kind", "cluster_id").to_dicts(),
        },
        "capacity_now": {
            "date": str(total["date"].max()),
            "total": now.to_dicts(),
            "clusters_sum": cluster_now.group_by(
                pl.col("cluster_id").str.starts_with("sea_").alias("sea")
            )
            .agg(pl.col("capacity_mw").sum())
            .to_dicts(),
        },
        "units_without_cluster_in_zone": zone_units.filter(pl.col("cluster_id").is_null()).height,
    }
    rep["entsoe"] = entsoe_report(silver, total, validation)
    validation.mkdir(parents=True, exist_ok=True)
    (validation / "mastr.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    return rep


def entsoe_report(mastr_silver: Path, total: pl.DataFrame, validation: Path) -> dict[str, Any]:
    root = mastr_silver.parent / "entsoe"
    cap_path, gen_path = root / "capacity", root / "generation"
    if not (cap_path / "_delta_log").exists():
        return {"available": False}
    rows = compare.capacity_vs_entsoe(total, pl.read_delta(str(cap_path)))
    out: dict[str, Any] = {
        "available": True,
        "threshold_pct": compare.THRESHOLD_PCT,
        "by_year": rows,
        "convention_mape": compare.convention_mape(rows),
    }
    if (gen_path / "_delta_log").exists():
        out["generation_vs_capacity"] = compare.generation_vs_capacity(
            pl.read_delta(str(gen_path)).select("ts_utc", "wind_offshore_mw", "wind_onshore_mw"),
            total,
        )
    return out


def read_report(validation: Path) -> dict[str, Any]:
    return json.loads((validation / "mastr.json").read_text())


__all__ = ["read_silver", "run"]
