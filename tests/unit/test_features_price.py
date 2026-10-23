# ruff: noqa: PLR2004, RUF003
"""Bloque `price`: variables del precio de D, resolución mixta, huecos y disponibilidad."""

import datetime as dt

import polars as pl
import pytest

from predico50hz.features import price, timing

UTC = dt.UTC
FIRST = dt.datetime(2026, 10, 16, 12, 56, tzinfo=UTC)


def _prices(d: dt.date, values: list[float], res: int, seen: dt.datetime) -> pl.DataFrame:
    lo, hi = price.delivery_window(d)
    step = dt.timedelta(minutes=res)
    n = (hi - lo) // step
    ts = [lo + i * step for i in range(n)]
    per_hour = 60 // res
    vals = [values[(i // per_hour) % len(values)] for i in range(n)]
    return pl.DataFrame(
        {
            "ts_utc": ts,
            "price_eur_mwh": vals,
            "resolution_min": [res] * n,
            "first_seen_at": [seen] * n,
        },
        schema={
            "ts_utc": pl.Datetime("us", "UTC"),
            "price_eur_mwh": pl.Float64,
            "resolution_min": pl.Int32,
            "first_seen_at": pl.Datetime("us", "UTC"),
        },
    )


def test_hourly_and_quarter_hour_give_the_same_variables() -> None:
    d = dt.date(2026, 7, 10)
    seen = dt.datetime(2026, 7, 9, 12, tzinfo=UTC)
    vals = [-5.0, 10.0, 20.0, 60.0]
    a = price.day_price(_prices(d, vals, 60, seen), d, FIRST)
    b = price.day_price(_prices(d, vals, 15, seen), d, FIRST)
    assert a.features == b.features
    assert a.features["price_neg_hours"] == 6.0  # una de cada 4 horas × 24
    assert a.features["price_min"] == -5.0
    assert a.features["price_range"] == 65.0
    assert a.features["price_mean"] == pytest.approx(21.25)
    assert not a.missing


def test_quarter_prices_are_averaged_per_hour_before_taking_min() -> None:
    d = dt.date(2026, 7, 10)
    lo, _ = price.delivery_window(d)
    df = _prices(d, [10.0], 15, dt.datetime(2026, 7, 9, 12, tzinfo=UTC))
    df = df.with_columns(price_eur_mwh=pl.when(pl.col("ts_utc") == lo).then(-100.0).otherwise(10.0))
    got = price.day_price(df, d, FIRST)
    assert got.features["price_min"] == pytest.approx((-100.0 + 30.0) / 4)  # media de la hora
    assert got.features["price_neg_hours"] == 1.0


@pytest.mark.parametrize(
    ("d", "hours"),
    [(dt.date(2026, 3, 29), 23), (dt.date(2025, 10, 26), 25), (dt.date(2026, 1, 5), 24)],
)
def test_day_with_dst_change_has_its_own_number_of_hours(d: dt.date, hours: int) -> None:
    lo, hi = price.delivery_window(d)
    assert (hi - lo) // dt.timedelta(hours=1) == hours
    got = price.day_price(_prices(d, [1.0], 60, dt.datetime(2020, 1, 1, tzinfo=UTC)), d, FIRST)
    assert not got.missing


def test_missing_hour_or_quarter_gives_nan_and_flag() -> None:
    d = dt.date(2026, 7, 10)
    seen = dt.datetime(2026, 7, 9, 12, tzinfo=UTC)
    full = _prices(d, [10.0], 15, seen)
    no_hour = full.filter(~pl.col("ts_utc").dt.hour().eq(5))
    no_quarter = full.slice(1)
    for df in (no_hour, no_quarter):
        got = price.day_price(df, d, FIRST)
        assert got.missing
        assert all(v != v for v in got.features.values())  # noqa: PLR0124 (NaN)
        assert got.available_at == timing.price_bound(d)


def test_availability_censored_measured_and_late() -> None:
    d = dt.date(2026, 10, 21)
    close = timing.closure_utc(d)
    # censurado: se vio en la primera descarga → cota prudente (cierre de D−1 + 6 h)
    cens = price.day_price(_prices(d, [5.0], 15, FIRST), d, FIRST)
    assert cens.available_at == timing.price_bound(d) == dt.datetime(2026, 10, 20, 19, tzinfo=UTC)
    # medido: la primera hora vista
    seen = dt.datetime(2026, 10, 20, 12, 0, 2, tzinfo=UTC)
    meas = price.day_price(_prices(d, [5.0], 15, seen), d, FIRST)
    assert meas.available_at == seen
    assert not meas.missing
    # medido pero llegó con el cierre ya pasado: no se usa
    late = price.day_price(_prices(d, [5.0], 15, close), d, FIRST)
    assert late.missing
    assert late.available_at < close


def test_bound_is_before_closure_in_every_dst_case() -> None:
    d0 = dt.date(2024, 4, 1)
    for i in range(1000):
        d = d0 + dt.timedelta(days=i)
        assert timing.closure_utc(d) - timing.price_bound(d) >= dt.timedelta(hours=17)
