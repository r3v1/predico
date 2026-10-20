"""Capacidad instalada diaria (UTC) por grupo (clúster) y total terrestre/marino.

Una unidad cuenta en el día `d` si `start <= d` y (`end` nulo o `d < end`): el día de la baja
definitiva ya no cuenta. Las paradas temporales NO restan: la unidad sigue instalada.
"""

import datetime as dt

import polars as pl


def daily_capacity(
    units: pl.DataFrame,
    group: str,
    start: dt.date,
    end: dt.date,
    value: str = "net_mw",
) -> pl.DataFrame:
    """(date, `group`, capacity_mw, n_units) para cada día de [start, end] y cada grupo.

    `units` necesita `group`, `value`, `start_date` (puesta en marcha) y `end_date` (baja o
    nulo). Las unidades sin `start_date` se ignoran; las anteriores a `start` se acumulan en
    el primer día.
    """
    u = units.filter(pl.col("start_date").is_not_null() & pl.col(group).is_not_null())
    groups = u.get_column(group).unique().sort()
    grid = pl.DataFrame({"date": pl.date_range(start, end, "1d", eager=True)}).join(
        pl.DataFrame({group: groups}), how="cross"
    )
    ups = u.select(
        pl.col(group),
        pl.col("start_date").clip(lower_bound=start).alias("date"),
        pl.col(value).alias("d_mw"),
        pl.lit(1, dtype=pl.Int64).alias("d_n"),
    )
    downs = u.filter(pl.col("end_date").is_not_null()).select(
        pl.col(group),
        pl.col("end_date").clip(lower_bound=start).alias("date"),
        (-pl.col(value)).alias("d_mw"),
        pl.lit(-1, dtype=pl.Int64).alias("d_n"),
    )
    events = (
        pl.concat([ups, downs])
        .filter(pl.col("date") <= end)
        .group_by(group, "date")
        .agg(pl.col("d_mw").sum(), pl.col("d_n").sum())
    )
    out = (
        grid.join(events, on=[group, "date"], how="left")
        .fill_null(0)
        .sort(group, "date")
        .with_columns(
            capacity_mw=pl.col("d_mw").cum_sum().over(group),
            n_units=pl.col("d_n").cum_sum().over(group),
        )
        .select("date", group, "capacity_mw", "n_units")
    )
    # Los acumulados en coma flotante pueden dejar residuos del orden de 1e-12.
    return out.with_columns(pl.col("capacity_mw").round(6).clip(lower_bound=0.0))
