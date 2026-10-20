"""Construcción de la tabla oro de variables: una fila por (`forecast_issue_date`, `ts_utc`).

Cada día D usa SOLO la pasada NWP 00Z de D, la capacidad MaStR de la fecha D (pesos) y el
calendario; la generación solo alimenta las columnas `y_*` (`target.py`). Nunca se lee el
pronóstico del operador ni el precio.
"""

import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from predico50hz.features import nwp, timing
from predico50hz.features.calendar import calendar_exprs
from predico50hz.features.capacity import (
    Cluster,
    capacity_features,
    day_capacity,
    load_clusters,
    read_cluster_capacity,
)
from predico50hz.features.config import (
    DEFAULT_CAP_CLUSTER,
    DEFAULT_CAP_TOTAL,
    DEFAULT_CENTROIDS,
    DEFAULT_GENERATION,
    DEFAULT_NWP,
    SPLIT_UNUSED,
    SPLITS,
    STEP,
)
from predico50hz.features.registry import KEY, build_registry
from predico50hz.features.target import build_target, read_capacity_total, read_generation
from predico50hz.features.variables import derive

DT_UTC = pl.Datetime("us", "UTC")


@dataclass(frozen=True)
class Sources:
    """Rutas de la plata que consume la capa de variables."""

    nwp: str = DEFAULT_NWP
    generation: str = DEFAULT_GENERATION
    cap_cluster: str = DEFAULT_CAP_CLUSTER
    cap_total: str = DEFAULT_CAP_TOTAL
    centroids: str = DEFAULT_CENTROIDS


def split_expr(col: str = "target_date") -> pl.Expr:
    """Partición v0.1 por fecha objetivo (meta, nunca feature)."""
    e = pl.when(pl.lit(False)).then(pl.lit(None, pl.String))
    for name, lo, hi in SPLITS:
        e = e.when(pl.col(col).is_between(lo, hi)).then(pl.lit(name))
    return e.otherwise(pl.lit(SPLIT_UNUSED)).alias("split_v01")


def build_day(  # noqa: PLR0913, PLR0917
    d: dt.date,
    pass_df: pl.DataFrame,
    clusters: list[Cluster],
    cap_cluster: pl.DataFrame,
    cap_total: pl.DataFrame,
    generation: pl.DataFrame | None,
) -> pl.DataFrame:
    """Filas de la tabla oro del cierre `d` (96, 92 o 100) con meta, variables y objetivo.

    Las variables solo usan `pass_df` (pasada 00Z de `d`), la capacidad de la fecha `d` y el
    calendario; `generation` solo entra en las columnas `y_*`.
    """
    grid = timing.target_grid(d)
    n = len(grid)
    ids = [c.point_id for c in clusters]
    cols = nwp.interpolate_pass(pass_df, ids, grid, d)
    cap = day_capacity(cap_cluster, clusters, d)
    feats, gust_missing = derive(cols, clusters, cap.weights)
    init = timing.nwp_init_time(d)
    mid = np.array([(t - init).total_seconds() + 450.0 for t in grid])
    feats["lead_hours"] = mid / 3600.0
    for k, v in capacity_features(cap).items():
        feats[k] = np.full(n, v)
    ts = pl.DataFrame({"ts_utc": grid}, schema={"ts_utc": DT_UTC})
    gen = None
    if generation is not None:
        gen = generation.filter(
            (pl.col("ts_utc") >= grid[0]) & (pl.col("ts_utc") < grid[-1] + STEP)
        )
    tgt = build_target(ts, gen, cap_total)
    meta = ts.with_columns(
        forecast_issue_date=pl.lit(d),
        target_date=pl.lit(d + dt.timedelta(days=1)),
        closure_utc=pl.lit(timing.closure_utc(d)).cast(DT_UTC),
        nwp_init_time=pl.lit(init).cast(DT_UTC),
        available_at_nwp=pl.lit(timing.available_at_nwp(d)).cast(DT_UTC),
        available_at_capacity=pl.lit(timing.available_at_capacity(d)).cast(DT_UTC),
        available_at_calendar=pl.lit(timing.available_at_calendar(d)).cast(DT_UTC),
        n_steps_day=pl.lit(n, pl.Int16),
        capacity_ffilled=tgt.get_column("capacity_ffilled") | pl.lit(cap.ffilled),
    ).with_columns(split_expr())
    calendar = ts.with_columns(calendar_exprs()).drop("ts_utc")
    return pl.concat(
        [
            meta,
            pl.DataFrame(feats).with_columns(gust_missing=pl.Series(gust_missing)),
            calendar,
            tgt.drop("ts_utc", "capacity_ffilled"),
        ],
        how="horizontal",
    )


def build_gold(
    src: Sources,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> tuple[pl.DataFrame, dict[str, object]]:
    """Tabla oro para las fechas de cierre con pasada disponible en `[start, end]`.

    Devuelve la tabla (columnas en el orden del registro, ordenada por clave) y un resumen con
    las fechas sin pasada. Sin pasada de D no hay filas de D (no se usa otra pasada).
    """
    clusters = load_clusters(src.centroids)
    ids = [c.point_id for c in clusters]
    root = Path(src.nwp)
    dates = [
        d
        for d in nwp.list_init_dates(root)
        if (start is None or d >= start) and (end is None or d <= end)
    ]
    cap_cluster = read_cluster_capacity(src.cap_cluster)
    cap_total = read_capacity_total(src.cap_total)
    generation = read_generation(src.generation)
    t0 = time.perf_counter()
    frames: list[pl.DataFrame] = []
    for d in dates:
        pass_df = nwp.read_pass(root, d, ids)
        if pass_df is not None:
            frames.append(build_day(d, pass_df, clusters, cap_cluster, cap_total, generation))
    if not frames:
        return pl.DataFrame(), {"passes": 0, "seconds": 0.0}
    df = pl.concat(frames).sort(KEY)
    names = [c.name for c in build_registry(clusters)]
    if set(names) != set(df.columns):
        raise ValueError(f"columnas distintas del registro: {set(names) ^ set(df.columns)}")
    info: dict[str, object] = {
        "passes": len(frames),
        "seconds": round(time.perf_counter() - t0, 2),
    }
    return df.select(names), info


def _range_filter(start: dt.date | None, end: dt.date | None) -> pl.Expr:
    e = pl.lit(True)
    if start is not None:
        e &= pl.col("forecast_issue_date") >= start
    if end is not None:
        e &= pl.col("forecast_issue_date") <= end
    return e


def write_gold(
    df: pl.DataFrame, path: Path, start: dt.date | None = None, end: dt.date | None = None
) -> bool:
    """Escritura Delta idempotente; devuelve `True` si creó una versión nueva.

    Con `start`/`end` (fechas de cierre) solo se reemplazan esas fechas (overwrite con
    predicado); sin ellas se reescribe todo. Si el contenido a escribir ya está, no escribe.
    """
    ranged = start is not None or end is not None
    exists = (path / "_delta_log").exists()
    if exists:
        cur = pl.read_delta(str(path))
        if ranged:
            cur = cur.filter(_range_filter(start, end))
        cur = cur.sort(KEY)
        same_shape = set(cur.columns) == set(df.columns) and cur.shape == df.shape
        if same_shape and cur.equals(df.sort(KEY).select(cur.columns)):
            return False
    if exists and ranged:
        parts: list[str] = []
        if start is not None:
            parts.append(f"forecast_issue_date >= '{start.isoformat()}'")
        if end is not None:
            parts.append(f"forecast_issue_date <= '{end.isoformat()}'")
        df.write_delta(
            str(path), mode="overwrite", delta_write_options={"predicate": " AND ".join(parts)}
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_delta(
            str(path), mode="overwrite", delta_write_options={"schema_mode": "overwrite"}
        )
    return True
