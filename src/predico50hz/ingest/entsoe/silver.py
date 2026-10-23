# ruff: noqa: PLR2004
"""Capa plata de ENTSO-E (Delta Lake), construida siempre desde el bronce.

Para cada clave gana el valor de la descarga más reciente. La escritura es idempotente: si la
tabla ya contiene exactamente el mismo contenido no se crea ninguna versión nueva, y las
claves son únicas (`KEYS`).

Generación: malla de 15 min en UTC con arrastre del último valor ("igual que Predico"). Cada
serie lleva `*_src` (`original`, `a03`, `hourly`, `gap`; nulo antes de su primer dato) y
`*_gap_len` (pasos consecutivos de la racha rellenada, 0 si no es relleno).
"""

import datetime as dt
from pathlib import Path

import polars as pl

from predico50hz.ingest.entsoe.bronze import list_chunks, read_chunk
from predico50hz.ingest.entsoe.datasets import (
    BIDDING_ZONE,
    CONTROL_AREA,
    WIND_OFFSHORE,
    WIND_ONSHORE,
)
from predico50hz.ingest.entsoe.parse import POINT_SCHEMA, parse_raw
from predico50hz.ingest.entsoe.pipeline import FALLBACK_SUFFIX

STEP = dt.timedelta(minutes=15)
KEYS: dict[str, list[str]] = {
    "generation": ["ts_utc"],
    "capacity": ["year", "psr_type"],
    "prices": ["ts_utc"],
    "forecast": ["ts_utc", "psr_type"],
}
_SERIES = {"offshore": WIND_OFFSHORE, "onshore": WIND_ONSHORE}


def load_points(root: Path, dataset: str) -> pl.DataFrame:
    """Todos los puntos de todas las descargas, con `fetched_at` y `fallback` (sin deduplicar)."""
    frames: list[pl.DataFrame] = []
    for c in list_chunks(root, dataset):
        if c.nodata:
            continue
        df = parse_raw(read_chunk(c))
        if not df.is_empty():
            frames.append(
                df.with_columns(
                    fetched_at=pl.lit(c.fetched_at).cast(pl.Datetime("us", "UTC")),
                    fallback=pl.lit(c.variant.endswith(FALLBACK_SUFFIX)),
                )
            )
    if not frames:
        schema = {**POINT_SCHEMA, "fetched_at": pl.Datetime("us", "UTC"), "fallback": pl.Boolean}
        return pl.DataFrame(schema=schema)
    return pl.concat(frames)


def latest(points: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """Un valor por clave: el de la descarga más reciente."""
    return points.sort("fetched_at", maintain_order=True).unique(key, keep="last").sort(key)


def _quarter_hour_obs(obs: pl.DataFrame) -> pl.DataFrame:
    """(ts_utc, value, src) a 15 min; los puntos de más de 15 min se repiten en cada cuarto."""
    kind = (
        pl.when(pl.col("resolution_min") > 15)
        .then(pl.lit("hourly"))
        .when(pl.col("implied"))
        .then(pl.lit("a03"))
        .otherwise(pl.lit("original"))
        .alias("src")
    )
    parts = obs.select("ts_utc", "value", "resolution_min", kind)
    coarse = parts.filter(pl.col("resolution_min") > 15).with_columns(
        k=pl.int_ranges(0, pl.col("resolution_min") // 15)
    )
    coarse = coarse.explode("k").with_columns(
        ts_utc=pl.col("ts_utc") + pl.duration(minutes=15) * pl.col("k")
    )
    fine = parts.filter(~(pl.col("resolution_min") > 15))
    return pl.concat(
        [fine.select("ts_utc", "value", "src"), coarse.select("ts_utc", "value", "src")]
    )


def _fill_series(obs: pl.DataFrame, grid: pl.Series) -> pl.DataFrame:
    """Reindexa en la malla con arrastre del último valor y marca los rellenos."""
    first = obs.get_column("ts_utc").min()
    df = pl.DataFrame({"ts_utc": grid}).join(obs, on="ts_utc", how="left")
    df = df.with_columns(
        is_gap=(pl.col("ts_utc") >= first) & pl.col("src").is_null(),
        value=pl.col("value").forward_fill(),
    )
    gap = pl.col("is_gap")
    run = (gap != gap.shift(fill_value=False)).cum_sum()
    return df.with_columns(
        src=pl.when(gap).then(pl.lit("gap")).otherwise(pl.col("src")),
        gap_len=pl.when(gap).then(pl.len().over(run)).otherwise(0).cast(pl.Int32),
    )


def build_generation(points: pl.DataFrame) -> pl.DataFrame:
    obs = latest(points, ["psr_type", "ts_utc"])
    per = {
        name: _quarter_hour_obs(obs.filter(pl.col("psr_type") == psr))
        for name, psr in _SERIES.items()
    }
    per = {k: v for k, v in per.items() if not v.is_empty()}
    if not per:
        return pl.DataFrame()
    firsts: list[dt.datetime] = [v["ts_utc"].min() for v in per.values()]  # pyright: ignore[reportAssignmentType]
    lasts: list[dt.datetime] = [v["ts_utc"].max() for v in per.values()]  # pyright: ignore[reportAssignmentType]
    grid = pl.datetime_range(
        min(firsts), min(lasts), STEP, time_zone="UTC", time_unit="us", eager=True
    ).alias("ts_utc")
    out = pl.DataFrame({"ts_utc": grid})
    for name, v in per.items():
        f = _fill_series(v.unique("ts_utc"), grid).select(
            "ts_utc",
            pl.col("value").alias(f"wind_{name}_mw"),
            pl.col("src").alias(f"{name}_src"),
            pl.col("gap_len").alias(f"{name}_gap_len"),
        )
        out = out.join(f, on="ts_utc", how="left")
    out = out.with_columns(
        wind_mw=pl.col("wind_offshore_mw") + pl.col("wind_onshore_mw"),
        is_gap_filled=(pl.col("offshore_src") == "gap") | (pl.col("onshore_src") == "gap"),
    )
    return out.sort("ts_utc")


def build_prices(points: pl.DataFrame) -> pl.DataFrame:
    pts = points.filter(pl.col("sequence").is_null() | (pl.col("sequence") == 1))
    first_seen = pts.group_by("ts_utc").agg(first_seen_at=pl.col("fetched_at").min())
    return (
        latest(pts, ["ts_utc"])
        .join(first_seen, on="ts_utc")
        .select(
            "ts_utc",
            pl.col("value").alias("price_eur_mwh"),
            "resolution_min",
            "fetched_at",
            "first_seen_at",
        )
    )


def build_forecast(points: pl.DataFrame) -> pl.DataFrame:
    first_seen = points.group_by(["ts_utc", "psr_type"]).agg(
        first_seen_at=pl.col("fetched_at").min()
    )
    return (
        latest(points, ["ts_utc", "psr_type"])
        .join(first_seen, on=["ts_utc", "psr_type"])
        .select(
            "ts_utc",
            "psr_type",
            pl.col("value").alias("forecast_mw"),
            "resolution_min",
            "doc_created",
            "doc_revision",
            "fetched_at",
            "first_seen_at",
        )
    )


def build_capacity(points: pl.DataFrame) -> pl.DataFrame:
    pts = points.with_columns(
        year=pl.col("ts_utc").dt.convert_time_zone("Europe/Berlin").dt.year().cast(pl.Int32),
        area=pl.when(pl.col("fallback")).then(pl.lit(BIDDING_ZONE)).otherwise(pl.lit(CONTROL_AREA)),
    )
    return latest(pts, ["year", "psr_type"]).select(
        "year", "psr_type", pl.col("value").alias("installed_mw"), "area", "fetched_at"
    )


BUILDERS = {
    "generation": build_generation,
    "capacity": build_capacity,
    "prices": build_prices,
    "forecast": build_forecast,
}


def silver_path(root: Path, dataset: str) -> Path:
    return root / dataset


def write_silver(df: pl.DataFrame, path: Path) -> bool:
    """Escritura idempotente; devuelve `True` si creó una versión nueva de la tabla."""
    if (path / "_delta_log").exists():
        current = pl.read_delta(str(path))
        if current.shape == df.shape and current.equals(df.select(current.columns)):
            return False
    df.write_delta(str(path), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    return True


def build_silver(dataset: str, bronze: Path, silver: Path) -> dict[str, object]:
    points = load_points(bronze, dataset)
    df = BUILDERS[dataset](points)
    if df.is_empty():
        return {"dataset": dataset, "rows": 0, "written": False}
    keys = KEYS[dataset]
    df = df.sort(keys)
    if df.select(keys).is_duplicated().any():
        raise ValueError(f"claves duplicadas en la plata de {dataset}")
    written = write_silver(df, silver_path(silver, dataset))
    return {"dataset": dataset, "rows": df.height, "written": written}
