# ruff: noqa: PLR2004
"""Tiempos de F3: cierre en UTC, ventana del día objetivo y malla de 15 min con cambio de hora."""

import datetime as dt
import itertools

import pytest

from predico50hz.features import timing

UTC = dt.UTC


def at(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


@pytest.mark.parametrize(
    ("d", "hour"),
    [
        (dt.date(2026, 7, 1), 13),  # verano
        (dt.date(2026, 1, 15), 14),  # invierno
        (dt.date(2026, 3, 28), 14),  # víspera del cambio a verano: aún invierno
        (dt.date(2026, 3, 29), 13),
        (dt.date(2025, 10, 25), 13),  # víspera del cambio a invierno: aún verano
        (dt.date(2025, 10, 26), 14),
    ],
)
def test_closure_utc(d: dt.date, hour: int) -> None:
    assert timing.closure_utc(d) == at(d.year, d.month, d.day, hour)


def test_winter_window_23_to_23() -> None:
    start, end = timing.target_window(dt.date(2026, 1, 15))
    assert (start, end) == (at(2026, 1, 15, 23), at(2026, 1, 16, 23))
    assert len(timing.target_grid(dt.date(2026, 1, 15))) == 96


def test_summer_window_22_to_22() -> None:
    start, end = timing.target_window(dt.date(2026, 7, 1))
    assert (start, end) == (at(2026, 7, 1, 22), at(2026, 7, 2, 22))
    assert len(timing.target_grid(dt.date(2026, 7, 1))) == 96


def test_spring_forward_has_92_steps() -> None:
    d = dt.date(2026, 3, 28)  # el día objetivo es el 2026-03-29
    grid = timing.target_grid(d)
    assert len(grid) == 92
    assert grid[0] == at(2026, 3, 28, 23)
    assert grid[-1] == at(2026, 3, 29, 21, 45)


def test_fall_back_has_100_steps() -> None:
    d = dt.date(2025, 10, 25)  # el día objetivo es el 2025-10-26
    grid = timing.target_grid(d)
    assert len(grid) == 100
    assert grid[0] == at(2025, 10, 25, 22)
    assert grid[-1] == at(2025, 10, 26, 22, 45)


def test_grid_is_contiguous_15_min() -> None:
    for d in (dt.date(2026, 3, 28), dt.date(2025, 10, 25), dt.date(2026, 5, 5)):
        g = timing.target_grid(d)
        assert {b - a for a, b in itertools.pairwise(g)} == {dt.timedelta(minutes=15)}


def test_availability_times() -> None:
    d = dt.date(2026, 7, 1)
    assert timing.nwp_init_time(d) == at(2026, 7, 1)
    assert timing.available_at_nwp(d) == at(2026, 7, 1, 8, 30)
    assert timing.available_at_capacity(d) == at(2026, 7, 1)
    assert timing.available_at_calendar(d) == at(2026, 7, 1)
    assert timing.available_at_nwp(d) < timing.closure_utc(d)
