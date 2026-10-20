"""Variables de calendario en hora local de Europe/Berlin (sin festivos: no hay dependencia).

Disponibilidad: deterministas, `available_at_calendar = D 00:00 UTC`.
"""

import polars as pl

from predico50hz.features.config import BERLIN


def calendar_exprs(ts: str = "ts_utc") -> list[pl.Expr]:
    """Expresiones de calendario a partir del inicio del intervalo `ts` (datetime UTC)."""
    local = pl.col(ts).dt.convert_time_zone(str(BERLIN))
    hour = local.dt.hour() + local.dt.minute() / 60.0
    two_pi = 2.0 * 3.141592653589793
    doy = (local.dt.ordinal_day() - 1) / 365.25
    return [
        hour.alias("hour_local"),
        (hour / 24.0 * two_pi).sin().alias("hour_sin"),
        (hour / 24.0 * two_pi).cos().alias("hour_cos"),
        (doy * two_pi).sin().alias("doy_sin"),
        (doy * two_pi).cos().alias("doy_cos"),
        (local.dt.weekday() - 1).cast(pl.Int8).alias("dow"),
        local.dt.month().cast(pl.Int8).alias("month"),
        (local.dt.weekday() >= 6).cast(pl.Int8).alias("is_weekend"),  # noqa: PLR2004
        (local.dt.dst_offset().dt.total_seconds() > 0).cast(pl.Int8).alias("is_dst"),
        pl.int_range(pl.len(), dtype=pl.Int16).alias("step_of_day"),
    ]
