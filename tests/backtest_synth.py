"""Datos sintéticos mínimos para las pruebas del marco de backtest (sin depender de `data/`)."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl

STEPS = 8  # cuartos de hora por día sintético (la malla real es de 96)
CAP = 10_000.0


def synth_gold(
    start: dt.date = dt.date(2025, 9, 2), end: dt.date = dt.date(2026, 10, 16), seed: int = 0
) -> pl.DataFrame:
    """Tabla oro reducida: viento → factor de capacidad con ruido, partición como la v0.1."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    d = start
    while d <= end:
        issue = d - dt.timedelta(days=1)
        split = (
            "train"
            if d <= dt.date(2026, 5, 31)
            else "valid"
            if d <= dt.date(2026, 7, 31)
            else "holdout"
        )
        day_wind = float(rng.uniform(3, 14))
        for s in range(STEPS):
            ws = max(day_wind + float(rng.normal(0, 0.5)), 0.0)
            cf = min(max((ws / 14.0) ** 3 + float(rng.normal(0, 0.02)), 0.0), 1.0)
            ts = dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC) + dt.timedelta(minutes=15 * s)
            rows.append(
                {
                    "forecast_issue_date": issue,
                    "target_date": d,
                    "ts_utc": ts,
                    "split_v01": split,
                    "n_steps_day": STEPS,
                    "month": d.month,
                    "hour_local": s * 0.25,
                    "wshub_all_mean": ws,
                    "y_cf": cf,
                    "y_gen_mw": cf * CAP,
                    "y_cap_mw": CAP,
                }
            )
        d += dt.timedelta(days=1)
    return pl.DataFrame(rows).with_columns(pl.col("ts_utc").dt.cast_time_unit("us"))


def write_operator(path: Path, gold: pl.DataFrame, noise: float = 300.0, seed: int = 1) -> None:
    """Delta con el «pronóstico del operador» sintético (B18 + B19) sobre la malla de `gold`."""
    rng = np.random.default_rng(seed)
    ts = gold["ts_utc"]
    base = gold["y_gen_mw"].to_numpy() + rng.normal(0, noise, gold.height)
    f = []
    for psr, share in (("B18", 0.2), ("B19", 0.8)):
        f.append(
            pl.DataFrame(
                {"ts_utc": ts, "psr_type": psr, "forecast_mw": np.maximum(base, 0) * share}
            )
        )
    pl.concat(f).write_delta(str(path))
