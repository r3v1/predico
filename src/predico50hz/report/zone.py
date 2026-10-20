# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# ruff: noqa: E501
"""Mapa de la zona de 50Hertz: aerogeneradores activos, grupos (clústeres) y rejilla meteorológica."""

import datetime as dt
import re
from pathlib import Path
from typing import Any

import polars as pl

from predico50hz.ingest.dynamical.config import DOMAIN
from predico50hz.report.paths import Paths
from predico50hz.report.util import has_delta

ACTIVE = ("In Betrieb", "Vorübergehend stillgelegt")
GRID_STEP = 0.25
BIN_DECIMALS = 2  # ~1 km: se agrupan turbinas casi coincidentes para aligerar el HTML


def cluster_name(cluster_id: str, kind: str, note: str) -> tuple[str, str]:
    """Nombre legible (corto y largo) a partir de la descripción del clúster."""
    if kind == "land":
        m = re.search(r"Landkreis dominante: (.+)$", note)
        state = note.split(",")[1].strip() if "," in note else ""
        name = m.group(1) if m else cluster_id
        return name, f"{name} ({state})" if state else name
    m = re.search(r"parques: (.+)$", note)
    parks = [re.sub(r"^OWP\s+", "", p.strip()).title() for p in m.group(1).split(",")] if m else []
    if not parks:
        return cluster_id, cluster_id
    return parks[0], " + ".join(parks) if len(parks) > 1 else parks[0]


def _growth(cap: pl.DataFrame, cluster_id: str) -> list[list[Any]]:
    """Potencia (MW) del grupo el 1 de enero de cada año y en la última fecha disponible."""
    sub = cap.filter(pl.col("cluster_id") == cluster_id).sort("date")
    if sub.is_empty():
        return []
    last = sub["date"].max()
    keep = sub.filter((pl.col("date").dt.month() == 1) & (pl.col("date").dt.day() == 1))
    pts = [
        [d.isoformat(), round(mw, 1)] for d, mw in keep.select("date", "capacity_mw").iter_rows()
    ]
    pts.append([last.isoformat(), round(float(sub["capacity_mw"][-1]), 1)])
    return pts


def aggregate(paths: Paths) -> dict[str, Any] | None:
    units_p = paths.silver / "mastr" / "units"
    clusters_p = paths.silver / "mastr" / "clusters"
    cap_p = paths.silver / "mastr" / "capacity_cluster"
    if not (has_delta(units_p) and has_delta(clusters_p) and has_delta(cap_p)):
        return None
    units = pl.read_delta(str(units_p)).filter(pl.col("in_zone") & pl.col("status").is_in(ACTIVE))
    clusters = pl.read_delta(str(clusters_p))
    cap = pl.read_delta(str(cap_p))

    pts = (
        units.with_columns(
            pl.col("lon").round(BIN_DECIMALS).alias("lon_b"),
            pl.col("lat").round(BIN_DECIMALS).alias("lat_b"),
        )
        .group_by("lon_b", "lat_b", "kind")
        .agg(pl.col("net_mw").sum().alias("mw"))
        .sort("lat_b", "lon_b")
    )
    hub = (
        units.filter(pl.col("hub_height_m").is_not_null())
        .group_by("cluster_id")
        .agg(
            ((pl.col("hub_height_m") * pl.col("net_mw")).sum() / pl.col("net_mw").sum()).alias(
                "hub"
            )
        )
    )
    hub_by = dict(hub.iter_rows())
    kind_stats = units.group_by("kind").agg(
        pl.len().alias("n"),
        pl.col("net_mw").sum().alias("mw"),
        (
            (pl.col("hub_height_m") * pl.col("net_mw")).sum()
            / pl.col("net_mw").filter(pl.col("hub_height_m").is_not_null()).sum()
        ).alias("hub"),
    )
    by_kind = {r["kind"]: r for r in kind_stats.iter_rows(named=True)}

    groups: list[dict[str, Any]] = []
    for r in clusters.sort("capacity_mw", descending=True).iter_rows(named=True):
        short, long = cluster_name(r["cluster_id"], r["kind"], r["note"])
        groups.append(
            {
                "id": r["cluster_id"],
                "name": short,
                "label": long,
                "state": r["state"],
                "kind": r["kind"],
                "lat": round(r["lat"], 4),
                "lon": round(r["lon"], 4),
                "mw": round(r["capacity_mw"], 1),
                "n": int(r["n_units"]),
                "hub_m": round(hub_by[r["cluster_id"]], 0) if r["cluster_id"] in hub_by else None,
                "growth": _growth(cap, r["cluster_id"]),
            }
        )
    last_date = cap["date"].max()
    return {
        "domain": {
            "lat_min": DOMAIN.lat_min,
            "lat_max": DOMAIN.lat_max,
            "lon_min": DOMAIN.lon_min,
            "lon_max": DOMAIN.lon_max,
            "step": GRID_STEP,
            "n_lat": round((DOMAIN.lat_max - DOMAIN.lat_min) / GRID_STEP) + 1,
            "n_lon": round((DOMAIN.lon_max - DOMAIN.lon_min) / GRID_STEP) + 1,
        },
        "turbines": [
            [r[0], r[1], round(r[2], 2), 1 if r[3] == "sea" else 0]
            for r in pts.select("lon_b", "lat_b", "mw", "kind").iter_rows()
        ],
        "n_turbines": units.height,
        "n_points": pts.height,
        "mw_total": round(float(units["net_mw"].sum()), 1),
        "land": {k: round(float(by_kind["land"][k] or 0), 1) for k in ("n", "mw", "hub")}
        if "land" in by_kind
        else None,
        "sea": {k: round(float(by_kind["sea"][k] or 0), 1) for k in ("n", "mw", "hub")}
        if "sea" in by_kind
        else None,
        "n_groups": len(groups),
        "groups": groups,
        "capacity_date": last_date.isoformat() if isinstance(last_date, dt.date) else None,
    }


def exists(root: Path) -> bool:
    return (root / "silver" / "mastr").exists()
