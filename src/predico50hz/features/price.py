# ruff: noqa: RUF002
"""Bloque `price`: precio day-ahead de D (el día del cierre), nunca el de D+1 (ADR 0031).

El precio de D se publica la víspera (en torno a las 12:00–13:00 UTC de D−1), más de 24 h antes
del cierre de D. El de D+1 llega después del cierre la mitad de los días y no entra: solo se leen
las filas de la plata dentro del día local D (`timing.target_window(D − 1)`).

**Resolución.** La plata guarda la nativa: horaria hasta el 30 sep 2025 y de 15 min desde el
1 oct 2025 (SDAC). Para que las variables signifiquen lo mismo en las dos épocas, el precio se
promedia primero por hora y todas las variables salen de esas 24 horas (23 o 25 con cambio de hora).

**Disponibilidad.** `available_at_price` es la primera hora en que la plata vio el día completo
(`first_seen_at`) si está medida; si no, la cota prudente `cierre(D−1) + 6 h`
(`timing.price_bound`).
Un día está medido cuando se vio después de la primera descarga completa; lo anterior está
censurado, porque ya estaba publicado al empezar a observar. Si el precio medido llegó a o tras el
cierre de D, no se usa: el día queda con NaN y `price_missing` = 1.
"""

import datetime as dt
from dataclasses import dataclass

import numpy as np
import polars as pl

from predico50hz.features import timing
from predico50hz.features.config import UTC

NAN = float("nan")
HOUR = dt.timedelta(hours=1)


def read_prices(path: str) -> pl.DataFrame:
    """Plata de precios: `ts_utc`, `price_eur_mwh`, `resolution_min`, `first_seen_at`."""
    return pl.read_delta(path).select("ts_utc", "price_eur_mwh", "resolution_min", "first_seen_at")


@dataclass(frozen=True)
class DayPrice:
    """Variables del precio de D, su disponibilidad y si el día está completo."""

    features: dict[str, float]
    available_at: dt.datetime
    missing: bool


def delivery_window(d: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Ventana UTC `[inicio, fin)` del día local D (el de entrega del precio de D)."""
    return timing.target_window(d - dt.timedelta(days=1))


def _hourly(prices: pl.DataFrame, lo: dt.datetime, hi: dt.datetime) -> pl.DataFrame | None:
    """Precio medio por hora de `[lo, hi)`; `None` si falta alguna hora o algún cuarto de hora."""
    day = prices.filter(pl.col("ts_utc").is_between(lo, hi, closed="left"))
    hours = (
        day.with_columns(hour=pl.col("ts_utc").dt.truncate("1h"))
        .group_by("hour")
        .agg(
            price=pl.col("price_eur_mwh").mean(),
            n=pl.len(),
            res=pl.col("resolution_min").min(),
            seen=pl.col("first_seen_at").max(),
        )
        .sort("hour")
    )
    expected = (hi - lo) // HOUR
    if hours.height != expected or hours.filter(pl.col("n") != 60 // pl.col("res")).height:
        return None
    return hours


def day_price(prices: pl.DataFrame, d: dt.date, first_fetch: dt.datetime) -> DayPrice:
    """Variables del precio de D con las filas del día local D (`first_fetch`: 1.ª descarga)."""
    lo, hi = delivery_window(d)
    bound = timing.price_bound(d)
    hours = _hourly(prices, lo, hi)
    absent = DayPrice(
        {"price_neg_hours": NAN, "price_min": NAN, "price_mean": NAN, "price_range": NAN},
        bound,
        True,
    )
    if hours is None:
        return absent
    seen: dt.datetime = hours.get_column("seen").max()  # pyright: ignore[reportAssignmentType]
    measured = seen > first_fetch
    if measured and seen >= timing.closure_utc(d):
        return absent  # llegó tarde: producción no lo habría tenido
    p = hours.get_column("price").to_numpy()
    feats = {
        "price_neg_hours": float(np.sum(p < 0.0)),
        "price_min": float(p.min()),
        "price_mean": float(p.mean()),
        "price_range": float(p.max() - p.min()),
    }
    return DayPrice(feats, seen if measured else bound, False)


def first_fetch_time(prices: pl.DataFrame) -> dt.datetime:
    """Primera descarga de la plata de precios: lo visto ahí ya estaba publicado (censurado)."""
    t = prices.get_column("first_seen_at").min()
    if not isinstance(t, dt.datetime):
        raise ValueError("plata de precios vacía")
    return t.astimezone(UTC)
