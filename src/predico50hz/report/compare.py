# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Gráfico «Un día visto por cada modelo» del capítulo 10.

Lee las predicciones de la última ejecución de validación de cada modelo (las mismas del
cuadro de mando), elige unos días representativos de cada mes y deja los datos compactos:
mallas de 30 min, MW enteros y solo los días elegidos. Si falta algo, devuelve `None`.
"""

import datetime as dt
import math
from typing import Any

import polars as pl

from predico50hz.features.timing import closure_utc, target_window
from predico50hz.report.paths import Paths
from predico50hz.report.util import has_delta, read_json

PRE_HOURS = 18  # horas de producción real antes del cierre que se dibujan
STEP_MIN = 30  # malla del gráfico (las métricas usan los 15 min originales)
KINDS = ("windy", "typical", "calm")  # orden en que se eligen y se muestran los días
NEEDED = ("ts_utc", "target_date", "y_mw", "q50_mw", "q10_mw", "q90_mw", "n_steps_day")


def _run_dir(paths: Paths, run_id: str):
    return paths.backtest_runs / run_id


def _load(paths: Paths, model: dict[str, Any]) -> pl.DataFrame | None:
    run_id = (model.get("validation") or {}).get("run_id")
    if not run_id:
        return None
    f = _run_dir(paths, run_id) / "predictions.parquet"
    try:
        df = pl.read_parquet(f)
    except OSError, pl.exceptions.PolarsError:
        return None
    return df if all(c in df.columns for c in NEEDED) else None


def lag_minutes(paths: Paths, models: list[dict[str, Any]]) -> int | None:
    """Minutos antes del cierre del último dato que usa «repetir lo último» (`lag_min`)."""
    for m in models:
        if m["model"] != "persistence":
            continue
        run_id = (m.get("validation") or {}).get("run_id")
        run = read_json(_run_dir(paths, run_id) / "run.json") if run_id else None
        lag = ((run or {}).get("params") or {}).get("lag_min")
        return int(lag) if isinstance(lag, int | float) else None
    return None


def pick_days(daily: pl.DataFrame) -> list[dict[str, Any]]:
    """Por mes: el día de más viento, el más tranquilo y el típico (el más cercano a la mediana).

    `daily` tiene `date` y `mean` (producción real media del día objetivo).
    """
    out: list[dict[str, Any]] = []
    df = daily.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month")).sort("date")
    for month in sorted(df["month"].unique()):
        sub = df.filter(pl.col("month") == month)
        med = sub["mean"].median()
        picks = {
            "windy": sub.sort("mean", descending=True)["date"][0],
            "calm": sub.sort("mean")["date"][0],
            "typical": sub.with_columns((pl.col("mean") - med).abs().alias("d")).sort("d")["date"][
                0
            ],
        }
        seen: set[dt.date] = set()
        for kind in KINDS:
            d = picks[kind]
            if d in seen:  # mes muy corto: sin duplicar días
                continue
            seen.add(d)
            out.append({"date": d, "month": month, "kind": kind})
    return out


def _grid(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Intervalo `[inicio, fin)` UTC del gráfico: 18 h antes del cierre hasta el fin del día D+1."""
    issue = day - dt.timedelta(days=1)
    return closure_utc(issue) - dt.timedelta(hours=PRE_HOURS), target_window(issue)[1]


def _rmse(a: pl.Series, b: pl.Series) -> float:
    return math.sqrt(((a - b) ** 2).mean())


def _round(xs: list[float | None]) -> list[int | None]:
    return [None if x is None or not math.isfinite(x) else round(x) for x in xs]


def aggregate(paths: Paths, models: list[dict[str, Any]]) -> dict[str, Any] | None:
    loaded = {m["model"]: _load(paths, m) for m in models}
    preds = {k: v for k, v in loaded.items() if v is not None and not v.is_empty()}
    if not preds:
        return None
    ref = preds["operator"] if "operator" in preds else next(iter(preds.values()))
    # solo días completos del modelo de referencia
    full = ref.filter(pl.col("n_steps_day") == pl.len().over("target_date"))
    daily = full.group_by("target_date").agg(pl.col("y_mw").mean().alias("mean"))
    daily = daily.rename({"target_date": "date"}).sort("date")
    if daily.is_empty():
        return None
    picks = pick_days(daily)

    gen_p = paths.silver / "entsoe" / "generation"
    gen = pl.read_delta(str(gen_p)).select("ts_utc", "wind_mw") if has_delta(gen_p) else None
    step = dt.timedelta(minutes=STEP_MIN)
    days: list[dict[str, Any]] = []
    for pk in picks:
        day = pk["date"]
        start, end = _grid(day)
        grid = pl.datetime_range(
            start, end, interval=f"{STEP_MIN}m", time_zone="UTC", closed="left", eager=True
        )
        base = pl.DataFrame({"ts_utc": grid})
        real = base
        if gen is not None:
            real = real.join(gen, on="ts_utc", how="left").rename({"wind_mw": "real"})
        else:
            real = real.with_columns(pl.lit(None, dtype=pl.Float64).alias("real"))
        tgt_ref = ref.filter(pl.col("target_date") == day).select("ts_utc", "y_mw")
        real = (
            real.join(tgt_ref, on="ts_utc", how="left")
            .with_columns(pl.coalesce("y_mw", "real").alias("real"))
            .select("ts_utc", "real")
        )
        issue = day - dt.timedelta(days=1)
        t_target = target_window(issue)[0]
        ti = int((t_target - start) / step)
        close = closure_utc(issue)
        rec: dict[str, Any] = {
            "date": day.isoformat(),
            "month": pk["month"],
            "kind": pk["kind"],
            "mean": round(float(daily.filter(pl.col("date") == day)["mean"][0])),
            "t0": int(start.timestamp() * 1000),
            "closure": int(close.timestamp() * 1000),
            "ti": ti,
            "real": _round(real["real"].to_list()),
            "m": {},
            "err": {},
        }
        for key, df in preds.items():
            d = df.filter(pl.col("target_date") == day).sort("ts_utc")
            if d.is_empty():
                continue
            rec["err"][key] = round(_rmse(d["q50_mw"], d["y_mw"]))
            g = base.join(d.select("ts_utc", "q50_mw", "q10_mw", "q90_mw"), on="ts_utc", how="left")
            g = g.slice(ti)
            entry: dict[str, Any] = {"q50": _round(g["q50_mw"].to_list())}
            if g["q10_mw"].null_count() < g.height:
                entry["lo"] = _round(g["q10_mw"].to_list())
                entry["hi"] = _round(g["q90_mw"].to_list())
            rec["m"][key] = entry
        days.append(rec)
    return {
        "step_min": STEP_MIN,
        "pre_hours": PRE_HOURS,
        "lag_min": lag_minutes(paths, models),
        "models": [m["model"] for m in models if m["model"] in preds],
        "days": days,
    }
