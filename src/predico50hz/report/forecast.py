# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""«La previsión meteorológica, por dentro»: campos, malla, plazos y escenarios del ensemble.

Lo que sale del bronce (solo lectura) se omite si el NAS no está montado; el resto del
capítulo sale de la configuración de la ingesta y de la plata.
"""

import datetime as dt
from itertools import pairwise
from typing import Any

import numpy as np
import polars as pl

from predico50hz.ingest.dynamical.config import (
    INIT_START,
    LEAD_HOURS,
    N_MEMBERS,
    SPEEDS,
    VARIABLES,
)
from predico50hz.ingest.dynamical.silver import ensemble_stats
from predico50hz.ingest.dynamical.validate import open_bronze_readonly
from predico50hz.ingest.ifs.extract import bilinear
from predico50hz.report.paths import Paths
from predico50hz.report.util import has_delta, read_json

DECIMALS = 1


def config_facts(paths: Paths) -> dict[str, Any]:
    """Variables, plazos y escenarios tal como los define la ingesta (sin leer datos)."""
    check = read_json(paths.stage0) or {}
    series = ((check.get("coverage") or {}).get("series") or {}).get("wind_gust_10m") or []
    gust_since = None
    for (_, prev), (day, cur) in pairwise(series):
        if prev > 0 and cur == 0:
            gust_since = day
            break
    return {
        "variables": list(VARIABLES),
        "derived": list(SPEEDS),
        "leads": list(LEAD_HOURS),
        "n_members": N_MEMBERS,
        "gust_since": gust_since,
    }


def _latest_init(root: Any) -> int | None:
    done = np.flatnonzero(np.asarray(root["ingested"][:]))
    return int(done[-1]) if done.size else None


def bronze_fields(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Campo medio del viento a 100 m y los escenarios de un punto, de la pasada más reciente."""
    try:
        root, snapshot = open_bronze_readonly(paths.nwp_bronze)
        i = _latest_init(root)
        if i is None:
            return None
        u = np.asarray(root["wind_u_100m"][i], dtype=np.float64)  # (paso, miembro, lat, lon)
        v = np.asarray(root["wind_v_100m"][i], dtype=np.float64)
        lats = np.asarray(root["latitude"][:], dtype=np.float64)
        lons = np.asarray(root["longitude"][:], dtype=np.float64)
    except Exception:  # NAS sin montar, almacén vacío o ilegible: el capítulo muestra la malla sola
        return None
    speed = np.hypot(u, v)
    mean = speed.mean(axis=1)  # (paso, lat, lon)
    init = INIT_START.date() + dt.timedelta(days=i)
    out: dict[str, Any] = {
        "init": init.isoformat(),
        "snapshot": snapshot,
        "lats": [round(float(x), 4) for x in lats],
        "lons": [round(float(x), 4) for x in lons],
        "steps": [np.round(m, DECIMALS).ravel().tolist() for m in mean],
        "vmax": float(np.ceil(np.nanmax(mean))),
        "members": None,
    }
    land = [g for g in groups if g["kind"] == "land"]
    if land:
        g = land[0]
        pu = bilinear(u, lats, lons, g["lat"], g["lon"])  # (paso, miembro)
        pv = bilinear(v, lats, lons, g["lat"], g["lon"])
        members = np.hypot(pu, pv).T  # (miembro, paso)
        stats = ensemble_stats(members, axis=0)
        out["members"] = {
            "group": {"id": g["id"], "name": g["label"]},
            "series": np.round(members, DECIMALS).tolist(),
            "stats": {k: np.round(s, 2).tolist() for k, s in stats.items()},
        }
    return out


def forecast_vs_production(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Viento previsto (ponderado por potencia, grupos en tierra) frente a la producción real."""
    root = paths.silver / "ifs_ens_clusters"
    land = {g["id"]: g["mw"] for g in groups if g["kind"] == "land"}
    gen_p = paths.silver / "entsoe" / "generation"
    if not root.is_dir() or not land or not has_delta(gen_p):
        return None
    cols = [f"wind_speed_100m_{s}" for s in ("p10", "p50", "p90")]
    w = pl.DataFrame({"point_id": list(land), "w": list(land.values())})
    df = (
        pl.scan_parquet(str(root / "init_date=*" / "*.parquet"))
        .select("init_time", "valid_time", "point_id", *cols)
        .filter(pl.col("point_id").is_in(list(land)))
        .collect()
        .with_columns(pl.col("init_time").dt.date().alias("init"))
        .filter(pl.col("valid_time").dt.date() == pl.col("init") + pl.duration(days=1))
        .join(w, on="point_id")
        .group_by("init")
        .agg(*[((pl.col(c) * pl.col("w")).sum() / pl.col("w").sum()).alias(c) for c in cols])
        .sort("init")
        .with_columns((pl.col("init") + pl.duration(days=1)).alias("day"))
    )
    if df.is_empty():
        return None
    gen = (
        pl.read_delta(str(gen_p))
        .with_columns(pl.col("ts_utc").dt.date().alias("day"))
        .group_by("day")
        .agg(pl.len().alias("n"), pl.col("wind_onshore_mw").mean().alias("prod"))
        .filter(pl.col("n") == 96)  # noqa: PLR2004
    )
    df = df.join(gen.select("day", "prod"), on="day", how="left")
    both = df.drop_nulls("prod")
    corr = (
        float(np.corrcoef(both["wind_speed_100m_p50"].to_numpy(), both["prod"].to_numpy())[0, 1])
        if both.height > 2  # noqa: PLR2004
        else None
    )
    return {
        "days": [d.isoformat() for d in df["day"]],
        "p10": [round(x, 2) for x in df["wind_speed_100m_p10"]],
        "p50": [round(x, 2) for x in df["wind_speed_100m_p50"]],
        "p90": [round(x, 2) for x in df["wind_speed_100m_p90"]],
        "prod": [None if x is None else round(x) for x in df["prod"]],
        "corr": None if corr is None else round(corr, 2),
        "n_days": df.height,
    }


def aggregate(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **config_facts(paths),
        "bronze": bronze_fields(paths, groups),
        "vs": forecast_vs_production(paths, groups),
    }
