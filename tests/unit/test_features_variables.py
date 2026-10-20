# ruff: noqa: PLR2004, RUF003
"""Variables derivadas: v³, E[v³], pesos por capacidad, densidad, cizalladura y dispersión."""

import datetime as dt

import numpy as np
import polars as pl
import pytest

from predico50hz.features.capacity import Cluster, capacity_features, day_capacity
from predico50hz.features.nwp import GUST_VAR, INSTANT_VARS
from predico50hz.features.variables import (
    air_density,
    derive,
    ev3,
    shear_exponent,
    weighted_std,
)

CLUSTERS = [Cluster("a", "land"), Cluster("b", "land"), Cluster("s", "sea")]


def cube(**over: list[float]) -> dict[str, np.ndarray]:
    """Una sola fila de tiempo, 3 clústeres; `over` fija columnas concretas."""
    c: dict[str, np.ndarray] = {}
    for v in (*INSTANT_VARS, GUST_VAR):
        for s in ("ctrl", "mean", "std", "p10", "p50", "p90"):
            c[f"{v}_{s}"] = np.full((1, 3), 1.0)
    c["pressure_surface_mean"] = np.full((1, 3), 101325.0)
    c["temperature_2m_mean"] = np.full((1, 3), 15.0)
    c["wind_speed_10m_mean"] = np.full((1, 3), 4.0)
    for k, v in over.items():
        c[k] = np.array([v], dtype=np.float64)
    return c


def cap_table(day: dt.date, mw: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [day] * 3, "cluster_id": [c.point_id for c in CLUSTERS], "capacity_mw": mw}
    )


def test_ev3_gaussian_third_moment() -> None:
    m, s = np.array([2.0]), np.array([1.0])
    assert ev3(m, s)[0] == 8.0 + 6.0  # μ³ + 3μσ²
    assert ev3(m, np.zeros(1))[0] == 8.0


def test_weights_follow_capacity_of_the_date() -> None:
    cap = pl.concat(
        [
            cap_table(dt.date(2026, 1, 1), [100.0, 300.0, 50.0]),
            cap_table(dt.date(2026, 1, 2), [300.0, 100.0, 50.0]),
        ]
    )
    w1 = day_capacity(cap, CLUSTERS, dt.date(2026, 1, 1))
    w2 = day_capacity(cap, CLUSTERS, dt.date(2026, 1, 2))
    np.testing.assert_allclose(w1.weights[0], [0.25, 0.75, 0.0])  # land
    np.testing.assert_allclose(w2.weights[0], [0.75, 0.25, 0.0])
    np.testing.assert_allclose(w1.weights[1], [0.0, 0.0, 1.0])  # sea
    np.testing.assert_allclose(w1.weights[2], np.array([100.0, 300.0, 50.0]) / 450.0)
    assert w1.group_mw.tolist() == [400.0, 50.0, 450.0]
    assert not w1.ffilled
    assert day_capacity(cap, CLUSTERS, dt.date(2026, 1, 5)).ffilled  # arrastra el último valor
    feats = capacity_features(w1)
    assert feats["cap_sea_share"] == pytest.approx(50.0 / 450.0)


def test_missing_capacity_raises() -> None:
    cap = cap_table(dt.date(2026, 1, 1), [1.0, 1.0, 1.0]).filter(pl.col("cluster_id") != "s")
    with pytest.raises(ValueError, match="sin capacidad"):
        day_capacity(cap, CLUSTERS, dt.date(2026, 1, 1))


def test_weighted_aggregates_and_v3_by_hand() -> None:
    cap = day_capacity(
        cap_table(dt.date(2026, 1, 1), [100.0, 300.0, 50.0]), CLUSTERS, dt.date(2026, 1, 1)
    )
    c = cube(
        wind_speed_100m_mean=[4.0, 8.0, 10.0],
        wind_speed_100m_ctrl=[5.0, 9.0, 11.0],
        wind_speed_100m_std=[1.0, 2.0, 0.5],
        wind_speed_100m_p10=[3.0, 6.0, 9.0],
        wind_speed_100m_p90=[6.0, 10.0, 12.0],
    )
    out, gm = derive(c, CLUSTERS, cap.weights)
    assert out["ws100_land_mean"][0] == pytest.approx(0.25 * 4 + 0.75 * 8)
    assert out["ws100_sea_mean"][0] == pytest.approx(10.0)
    assert out["ws100_all_mean"][0] == pytest.approx((400 + 2400 + 500) / 450.0)
    assert out["v3_land_ctrl"][0] == pytest.approx(0.25 * 125 + 0.75 * 729)
    assert out["v3_land_ens"][0] == pytest.approx(
        0.25 * (64 + 3 * 4 * 1) + 0.75 * (512 + 3 * 8 * 4)
    )
    assert out["v3_land_p10"][0] == pytest.approx(0.25 * 27 + 0.75 * 216)
    assert out["v3_land_p90"][0] == pytest.approx(0.25 * 216 + 0.75 * 1000)
    assert out["ws100_land_std"][0] == pytest.approx(0.25 * 1 + 0.75 * 2)
    assert out["ws100_a_mean"][0] == 4.0
    assert gm.tolist() == [0]
    # Dispersión espacial entre clústeres terrestres (pesos 0,25 y 0,75).
    mu = 0.25 * 4 + 0.75 * 8
    sd = np.sqrt(0.25 * (4 - mu) ** 2 + 0.75 * (8 - mu) ** 2)
    assert out["disp_land_std"][0] == pytest.approx(sd)
    assert out["disp_land_range"][0] == pytest.approx(4.0)
    assert out["disp_land_cv"][0] == pytest.approx(sd / mu)
    assert out["ws100_sea_minus_land"][0] == pytest.approx(10.0 - mu)


def test_air_density_standard_atmosphere() -> None:
    rho = air_density(np.array([101325.0]), np.array([15.0]))
    assert rho[0] == pytest.approx(1.2250, abs=1e-3)


def test_shear_exponent_clips_and_default() -> None:
    a = shear_exponent(np.array([8.0, 20.0, 2.0, 0.4, 8.0]), np.array([4.0, 2.0, 4.0, 3.0, 0.3]))
    assert a[0] == pytest.approx(np.log(2.0) / np.log(10.0))
    assert a[1] == 0.5  # ln(10)/ln(10) = 1, recortado a 0,5
    assert a[2] == 0.0  # v100 < v10: negativo, recortado a 0
    assert a[3] == pytest.approx(1 / 7)  # v100 < 0,5 m/s
    assert a[4] == pytest.approx(1 / 7)  # v10 < 0,5 m/s


def test_hub_speed_and_power_density_proxy() -> None:
    cap = day_capacity(
        cap_table(dt.date(2026, 1, 1), [1.0, 1.0, 1.0]), CLUSTERS, dt.date(2026, 1, 1)
    )
    c = cube(wind_speed_100m_mean=[8.0, 8.0, 8.0], wind_speed_100m_std=[0.0, 0.0, 0.0])
    out, _ = derive(c, CLUSTERS, cap.weights)
    alpha = np.log(8.0 / 4.0) / np.log(10.0)
    assert out["wshub_a_mean"][0] == pytest.approx(8.0 * 1.19**alpha)
    assert out["wshub_s_mean"][0] == pytest.approx(8.0 * 1.04**alpha)
    rho = 101325.0 / (287.05 * 288.15)
    assert out["pdens_sea"][0] == pytest.approx(rho * (8.0 * 1.04**alpha) ** 3)
    assert out["shear_all"][0] == pytest.approx(alpha)


def test_stability_direction_and_gust() -> None:
    cap = day_capacity(
        cap_table(dt.date(2026, 1, 1), [1.0, 1.0, 1.0]), CLUSTERS, dt.date(2026, 1, 1)
    )
    c = cube(
        temperature_925hpa_mean=[5.0, 5.0, 5.0],
        temperature_2m_mean=[8.0, 8.0, 8.0],
        wind_u_100m_mean=[3.0, 3.0, 3.0],
        wind_v_100m_mean=[4.0, 4.0, 4.0],
        wind_gust_10m_mean=[8.0, 8.0, 8.0],
    )
    out, gm = derive(c, CLUSTERS, cap.weights)
    assert out["stab_all"][0] == pytest.approx(-3.0)
    assert out["wdir100_all_sin"][0] == pytest.approx(-0.6)  # viento del suroeste: sin = -u/|V|
    assert out["wdir100_all_cos"][0] == pytest.approx(-0.8)
    assert out["gustfac_all"][0] == pytest.approx(8.0 / 4.0)
    assert gm.tolist() == [0]
    c["wind_gust_10m_mean"] = np.full((1, 3), np.nan)
    c["wind_gust_10m_p90"] = np.full((1, 3), np.nan)
    out, gm = derive(c, CLUSTERS, cap.weights)
    assert gm.tolist() == [1]
    assert np.isnan(out["gust_all_mean"]).all()
    assert np.isnan(out["gustfac_all"]).all()
    assert not np.isnan(out["ws100_all_mean"]).any()


def test_weighted_std_matches_numpy() -> None:
    x = np.array([[1.0, 2.0, 6.0]])
    w = np.array([0.5, 0.25, 0.25])
    mu = (x @ w)[0]
    assert weighted_std(x, w)[0] == pytest.approx(np.sqrt(((x[0] - mu) ** 2 * w).sum()))
