"""Objetivo (F3.3): generación en MW y factor de capacidad del intervalo de 15 min.

NO es una variable: la generación y el denominador del día del intervalo solo entran en las
columnas `y_*`. Denominador: capacidad MaStR de la fecha UTC de `ts_utc`; más allá del último
día de MaStR se arrastra el último valor y se marca `capacity_ffilled`.
"""

import polars as pl

UTC = pl.Datetime("us", "UTC")


def read_generation(path: str) -> pl.DataFrame:
    """Plata de generación de ENTSO-E (`ts_utc` = inicio del intervalo, MW)."""
    return pl.read_delta(path).select(
        "ts_utc", "wind_mw", "wind_onshore_mw", "wind_offshore_mw", "is_gap_filled"
    )


def read_capacity_total(path: str) -> pl.DataFrame:
    """Capacidad diaria MaStR por tipo, en ancho: `date`, `cap_land_mw`, `cap_sea_mw`."""
    t = pl.read_delta(path).select("date", "kind", "capacity_mw")
    return (
        t.pivot(on="kind", index="date", values="capacity_mw")
        .rename({"land": "cap_land_mw", "sea": "cap_sea_mw"})
        .sort("date")
    )


def target_capacity(ts: pl.DataFrame, cap_total: pl.DataFrame) -> pl.DataFrame:
    """Añade a `ts` (`ts_utc`) la capacidad de su fecha UTC y `capacity_ffilled` (MW)."""
    last = cap_total.get_column("date").max()
    capped = ts.with_columns(date=pl.col("ts_utc").dt.date())
    ffilled = pl.col("date") > last
    joined = capped.with_columns(date_used=pl.min_horizontal(pl.col("date"), pl.lit(last))).join(
        cap_total, left_on="date_used", right_on="date", how="left"
    )
    return joined.select(
        "ts_utc",
        pl.col("cap_land_mw").alias("y_cap_land_mw"),
        pl.col("cap_sea_mw").alias("y_cap_sea_mw"),
        (pl.col("cap_land_mw") + pl.col("cap_sea_mw")).alias("y_cap_mw"),
        ffilled.alias("capacity_ffilled"),
    )


def build_target(
    ts: pl.DataFrame, generation: pl.DataFrame | None, cap_total: pl.DataFrame
) -> pl.DataFrame:
    """Columnas `y_*` y `capacity_ffilled` para la malla `ts` (`ts_utc`, UTC).

    Nulos solo donde falta la generación (por ejemplo, el último día parcial de ENTSO-E).
    """
    cap = target_capacity(ts, cap_total)
    gen = (
        generation
        if generation is not None
        else pl.DataFrame(
            schema={
                "ts_utc": UTC,
                "wind_mw": pl.Float64,
                "wind_onshore_mw": pl.Float64,
                "wind_offshore_mw": pl.Float64,
                "is_gap_filled": pl.Boolean,
            }
        )
    )
    out = cap.join(gen, on="ts_utc", how="left")
    return out.select(
        "ts_utc",
        pl.col("wind_mw").alias("y_gen_mw"),
        pl.col("wind_onshore_mw").alias("y_gen_land_mw"),
        pl.col("wind_offshore_mw").alias("y_gen_sea_mw"),
        "y_cap_mw",
        "y_cap_land_mw",
        "y_cap_sea_mw",
        (pl.col("wind_mw") / pl.col("y_cap_mw")).alias("y_cf"),
        (pl.col("wind_onshore_mw") / pl.col("y_cap_land_mw")).alias("y_cf_land"),
        (pl.col("wind_offshore_mw") / pl.col("y_cap_sea_mw")).alias("y_cf_sea"),
        pl.col("is_gap_filled").alias("y_is_gap_filled"),
        "capacity_ffilled",
    )
