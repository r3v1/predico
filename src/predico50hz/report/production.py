# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Producción eólica real, crecimiento de la potencia y un abanico de previsión de ejemplo."""

import datetime as dt
from typing import Any

import polars as pl

from predico50hz.report.paths import Paths
from predico50hz.report.util import has_delta

STEPS_PER_DAY = 96  # cuartos de hora
EXAMPLE_DAYS = 14
STATS = ("p10", "p50", "p90", "mean")


def daily_production(paths: Paths) -> dict[str, Any] | None:
    gen_p = paths.silver / "entsoe" / "generation"
    cap_p = paths.silver / "mastr" / "capacity_total"
    if not has_delta(gen_p):
        return None
    gen = (
        pl.read_delta(str(gen_p))
        .with_columns(pl.col("ts_utc").dt.date().alias("date"))
        .group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.col("wind_onshore_mw").mean().alias("on"),
            pl.col("wind_offshore_mw").mean().alias("off"),
        )
        .filter(pl.col("n") == STEPS_PER_DAY)  # solo días completos
        .sort("date")
    )
    if gen.is_empty():
        return None
    out: dict[str, Any] = {
        "start": gen["date"][0].isoformat(),
        "end": gen["date"][-1].isoformat(),
        "onshore": [round(x) for x in gen["on"].fill_null(0)],
        "offshore": [round(x) for x in gen["off"].fill_null(0)],
        "cap_onshore": None,
        "cap_offshore": None,
        "n_days": gen.height,
    }
    if has_delta(cap_p):
        cap = pl.read_delta(str(cap_p))
        for kind, key in (("land", "cap_onshore"), ("sea", "cap_offshore")):
            series = (
                gen.select("date")
                .join(
                    cap.filter(pl.col("kind") == kind).select("date", "capacity_mw"),
                    on="date",
                    how="left",
                )
                .with_columns(pl.col("capacity_mw").forward_fill())
            )
            out[key] = [None if x is None else round(x) for x in series["capacity_mw"]]
        # factor de capacidad medio de los últimos 365 días completos
        last = gen.tail(365)
        for kind, col, key in (("land", "on", "cf_onshore"), ("sea", "off", "cf_offshore")):
            c = (
                last.select("date", col)
                .join(
                    cap.filter(pl.col("kind") == kind).select("date", "capacity_mw"),
                    on="date",
                    how="left",
                )
                .drop_nulls()
            )
            out[key] = round(float((c[col] / c["capacity_mw"]).mean()), 3) if c.height else None
        out["sea_jump"] = _sea_jump(cap)
    peak = (
        gen.with_columns((pl.col("on") + pl.col("off")).alias("tot"))
        .sort("tot", descending=True)
        .row(0, named=True)
    )
    out["peak"] = {"date": peak["date"].isoformat(), "mw": round(peak["tot"])}
    return out


def _sea_jump(cap: pl.DataFrame, window: int = 90, lookback: int = 365) -> dict[str, Any] | None:
    """Mayor subida de la potencia marina en una ventana de `window` días durante el último año."""
    sea = cap.filter(pl.col("kind") == "sea").sort("date").tail(lookback + window)
    if sea.height <= window:
        return None
    s = sea.with_columns(pl.col("capacity_mw").shift(window).alias("before")).drop_nulls()
    s = s.with_columns((pl.col("capacity_mw") / pl.col("before") - 1).alias("rise"))
    best = s.sort("rise", descending=True).row(0, named=True)
    return {
        "pct": round(best["rise"] * 100, 1),
        "now_mw": round(best["capacity_mw"]),
        "before_mw": round(best["before"]),
        "date": best["date"].isoformat(),
    }


def capacity_stack(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    cap_p = paths.silver / "mastr" / "capacity_cluster"
    if not has_delta(cap_p) or not groups:
        return None
    cap = (
        pl.read_delta(str(cap_p))
        .with_columns(pl.col("date").dt.truncate("1mo").alias("month"))
        .sort("date")
        .group_by("month", "cluster_id", maintain_order=True)
        .agg(pl.col("capacity_mw").last())
    )
    months = sorted(cap["month"].unique().to_list())
    series = []
    ordered = sorted(groups, key=lambda g: (g["kind"] == "sea", g["state"], -g["mw"]))
    for g in ordered:
        sub = dict(
            cap.filter(pl.col("cluster_id") == g["id"]).select("month", "capacity_mw").iter_rows()
        )
        series.append(
            {
                "id": g["id"],
                "name": g["label"],
                "kind": g["kind"],
                "state": g["state"],
                "data": [round(sub.get(m, 0.0)) for m in months],
            }
        )
    return {"months": [m.isoformat() for m in months], "series": series}


def _pick_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mayor grupo terrestre, mayor de otro estado y mayor marino (los tres más informativos)."""
    land = [g for g in groups if g["kind"] == "land"]
    sea = [g for g in groups if g["kind"] == "sea"]
    picks = land[:1]
    picks += [g for g in land if picks and g["state"] != picks[0]["state"]][:1]
    return picks + sea[:1]


def example_forecast(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    root = paths.silver / "ifs_ens_clusters"
    parts = sorted(root.glob("init_date=*")) if root.is_dir() else []
    picks = _pick_groups(groups)
    if not parts or not picks:
        return None
    cols = [f"wind_speed_100m_{s}" for s in STATS]
    ids = [g["id"] for g in picks]
    days: list[dict[str, Any]] = []
    for part in parts[-EXAMPLE_DAYS:]:
        files = sorted(part.glob("*.parquet"))
        if not files:
            continue
        df = pl.read_parquet(files[0], columns=["init_time", "valid_time", "point_id", *cols])
        init = df["init_time"][0]
        entry: dict[str, Any] = {"init": init.date().isoformat(), "groups": {}}
        for gid in ids:
            sub = df.filter(pl.col("point_id") == gid).sort("valid_time")
            if sub.is_empty():
                continue
            entry["valid"] = [v.strftime("%Y-%m-%dT%H:%M:%SZ") for v in sub["valid_time"]]
            entry["groups"][gid] = {
                s: [round(float(x), 2) for x in sub[f"wind_speed_100m_{s}"]] for s in STATS
            }
        if entry["groups"]:
            days.append(entry)
    if not days:
        return None
    return {
        "groups": [{"id": g["id"], "name": g["label"], "kind": g["kind"]} for g in picks],
        "days": days,
        "n_members": 51,
    }


def aggregate(paths: Paths, groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "daily": daily_production(paths),
        "stack": capacity_stack(paths, groups),
        "example": example_forecast(paths, groups),
    }


def today_utc() -> dt.date:
    return dt.datetime.now(dt.UTC).date()
