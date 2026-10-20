# ruff: noqa: PLR2004
"""Interpolación 3 h → 15 min: lineal en el punto medio, racha constante por tramos."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from conftest import POINTS, pass_frame
from predico50hz.features import interpolate as itp
from predico50hz.features import timing
from predico50hz.features.nwp import interpolate_pass


def test_linear_by_hand() -> None:
    x = np.array([0.0, 3.0, 6.0]) * 3600
    y = np.array([0.0, 3.0, 9.0])
    q = np.array([1.5, 3.0, 4.5]) * 3600
    assert itp.linear(x, y, q).tolist() == [1.5, 3.0, 6.0]


def test_linear_is_convex_combination() -> None:
    x = np.array([0.0, 10.0])
    y = np.array([[1.0, 5.0], [3.0, 2.0]])  # (nodos, 2 columnas)
    out = itp.linear(x, y, np.linspace(0, 10, 25))
    assert out.min(axis=0).tolist() == [1.0, 2.0]
    assert out.max(axis=0).tolist() == [3.0, 5.0]


def test_no_extrapolation() -> None:
    x = np.array([0.0, 10.0])
    with pytest.raises(ValueError, match="fuera del rango"):
        itp.linear(x, np.array([1.0, 2.0]), np.array([11.0]))


def test_pchip_does_not_overshoot() -> None:
    x = np.arange(5.0)
    y = np.array([0.0, 0.0, 10.0, 10.0, 10.0])
    out = itp.interpolate("pchip", x, y, np.linspace(0, 4, 41))
    assert out.min() >= 0.0
    assert out.max() <= 10.0


def test_gust_is_piecewise_constant_with_next_step() -> None:
    x = np.array([18.0, 21.0, 24.0]) * 3600
    y = np.array([1.0, 2.0, 3.0])
    ends = np.array([18.25, 20.75, 21.0, 21.25, 24.0]) * 3600
    # [18:00,18:15) → paso 21 (ventana (18,21]); [20:45,21:00) → 21; [21:00,21:15) → 24.
    assert itp.step_after(x, y, ends).tolist() == [2.0, 2.0, 2.0, 3.0, 3.0]


def test_interpolate_pass_ramp_and_gust() -> None:
    d = dt.date(2026, 1, 15)
    ids = [p[0] for p in POINTS]
    df = pass_frame(d)
    # Rampa: velocidad media = horas de plazo; racha media = horas de plazo.
    ramp = pl.col("lead_hours").cast(pl.Float32)
    df = df.with_columns(
        wind_speed_100m_mean=ramp,
        wind_gust_10m_mean=ramp,
        wind_speed_100m_p10=ramp - 1,
        wind_speed_100m_p50=ramp,
        wind_speed_100m_p90=ramp + 1,
    )
    grid = timing.target_grid(d)[:5]  # 23:00, 23:15, 23:30, 23:45, 00:00 (lead 23..24)
    cols = interpolate_pass(df, ids, grid, d)
    mids = [23.125, 23.375, 23.625, 23.875, 24.125]
    np.testing.assert_allclose(cols["wind_speed_100m_mean"][:, 0], mids, atol=1e-9)
    # Racha: 23:00..23:45 → paso 24; 00:00 (24:00-24:15) → paso 27.
    assert cols["wind_gust_10m_mean"][:, 1].tolist() == [24, 24, 24, 24, 27]
    assert np.all(cols["wind_speed_100m_p10"] <= cols["wind_speed_100m_p50"])


def test_interpolate_pass_keeps_nan_gust() -> None:
    d = dt.date(2026, 1, 15)
    ids = [p[0] for p in POINTS]
    cols = interpolate_pass(pass_frame(d, with_gust=False), ids, timing.target_grid(d), d)
    assert np.isnan(cols["wind_gust_10m_mean"]).all()
    assert not np.isnan(cols["wind_speed_100m_mean"]).any()
