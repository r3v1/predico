# ruff: noqa: RUF003, PLR2004
"""Métricas del backtest: casos calculados a mano y propiedades (sin hypothesis)."""

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from predico50hz.evaluation import metrics as mt

# y = 10, 20, 30, 40; Q10, Q50, Q90 con dos fallos de intervalo (20 < 22 y 40 > 36).
Y = np.array([10.0, 20.0, 30.0, 40.0])
LO = np.array([8.0, 22.0, 25.0, 30.0])
MID = np.array([10.0, 21.0, 30.0, 38.0])
HI = np.array([12.0, 28.0, 35.0, 36.0])


def test_point_metrics_by_hand() -> None:
    # errores m − y = 0, 1, 0, −2
    assert mt.rmse(Y, MID) == pytest.approx(math.sqrt(5 / 4))
    assert mt.mae(Y, MID) == pytest.approx(0.75)
    assert mt.bias(Y, MID) == pytest.approx(-0.25)


def test_winkler_by_hand() -> None:
    # anchos 4, 6, 10, 6; excesos: 20 por debajo de 22 (2) y 40 por encima de 36 (4); 2/α = 10
    assert mt.winkler_scores(Y, LO, HI).tolist() == pytest.approx([4, 26, 10, 46])
    assert mt.mean_winkler(Y, LO, HI) == pytest.approx(21.5)
    assert mt.coverage(Y, LO, HI) == pytest.approx(0.5)
    assert mt.mean_width(LO, HI) == pytest.approx(6.5)


def test_winkler_alpha_changes_penalty() -> None:
    # con α = 0,5 el factor es 4: (4) + (6+8) + 10 + (6+16) = 50 → 12,5
    assert mt.mean_winkler(Y, LO, HI, alpha=0.5) == pytest.approx(12.5)


def test_boundaries_are_covered() -> None:
    assert mt.coverage(np.array([1.0, 3.0]), np.array([1.0, 1.0]), np.array([2.0, 3.0])) == 1.0


def _frame(
    day1: tuple[list[float], list[float], list[float]],
    day2: tuple[list[float], list[float], list[float]],
) -> pl.DataFrame:
    rows = []
    for k, (y, m, op) in enumerate((day1, day2)):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=k)
        for i in range(4):
            ts = dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC) + dt.timedelta(minutes=15 * i)
            rows.append(
                {
                    "target_date": d,
                    "ts_utc": ts,
                    "n_steps_day": 4,
                    "y_mw": y[i],
                    "q10_mw": None,
                    "q50_mw": m[i],
                    "q90_mw": None,
                    "op_q50_mw": op[i],
                }
            )
    schema = {"q10_mw": pl.Float64, "q90_mw": pl.Float64}
    return pl.DataFrame(rows, schema_overrides=schema).with_columns(
        pl.col("ts_utc").dt.cast_time_unit("us")
    )


def test_summarize_peak_ranking_and_no_interval() -> None:
    df = _frame(
        (
            [1, 5, 3, 2],
            [1, 3, 5, 2],
            [1, 5, 3, 2],
        ),  # día 1: pico real a las 00:15, previsto a las 00:30
        (
            [2, 4, 6, 3],
            [1, 3, 4, 2],
            [0, 0, 0, 0],
        ),  # día 2: pico real 6 y previsto 4, ambos en el 3.er paso
    )
    s = mt.summarize(df)
    # errores día 1: 0, −2, 2, 0 → RMSE día 1 = √2; día 2: −1, −1, −2, −1 → √(7/4)
    assert s["rmse"] == pytest.approx(math.sqrt((0 + 4 + 4 + 0 + 1 + 1 + 4 + 1) / 8))
    assert s["n_days"] == 2
    assert s["peak_abs_error_mw"] == pytest.approx(1.0)  # |5−5| = 0 y |4−6| = 2
    assert s["peak_bias_mw"] == pytest.approx(-1.0)
    assert s["peak_shift_h"] == pytest.approx(0.125)  # 0,25 h y 0 h
    # operador: día 1 perfecto (RMSE 0) gana; día 2 con RMSE 4,03 pierde frente al modelo (1,32)
    assert s["days_better_than_operator_pct"] == pytest.approx(50.0)
    assert s["n_days_ranked"] == 2
    # sin cuantiles: las métricas de intervalo quedan nulas, no inventadas
    assert s["winkler"] is None
    assert s["coverage"] is None
    assert s["mean_width"] is None


def test_summarize_with_interval_and_operator_flag() -> None:
    df = _frame(
        ([1, 5, 3, 2], [1, 3, 5, 2], [1, 5, 3, 2]), ([2, 4, 6, 3], [1, 3, 4, 2], [0, 0, 0, 0])
    )
    df = df.with_columns(
        (pl.col("q50_mw") - 1).alias("q10_mw"), (pl.col("q50_mw") + 1).alias("q90_mw")
    )
    s = mt.summarize(df, rank_vs_operator=False)
    assert s["mean_width"] == pytest.approx(2.0)
    assert s["days_better_than_operator_pct"] is None
    # aciertos día 1: sí, no, no, sí; día 2: sí, sí, no, sí (5 de 8)
    assert s["coverage"] == pytest.approx(5 / 8)


def test_incomplete_days_do_not_count_for_peaks() -> None:
    df = _frame(
        ([1, 5, 3, 2], [1, 3, 5, 2], [1, 5, 3, 2]), ([2, 4, 6, 3], [1, 3, 4, 2], [0, 0, 0, 0])
    )
    s = mt.summarize(df.filter(pl.col("ts_utc") < dt.datetime(2026, 1, 2, 0, 45, tzinfo=dt.UTC)))
    assert s["n_days"] == 1  # el día 2 queda con 3 de 4 pasos


@pytest.mark.parametrize("seed", range(20))
def test_properties(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 200
    y = rng.uniform(0, 1000, n)
    m = y + rng.normal(0, 50, n)
    lo = m - rng.uniform(0, 80, n)
    hi = m + rng.uniform(0, 80, n)
    assert mt.rmse(y, m) >= mt.mae(y, m) - 1e-9  # desigualdad entre normas
    assert 0.0 <= mt.coverage(y, lo, hi) <= 1.0
    assert mt.mean_winkler(y, lo, hi) >= mt.mean_width(lo, hi) - 1e-9  # penaliza, nunca resta
    assert mt.rmse(y, y) == 0.0
    c = 3.7  # equivarianza de escala: RMSE y Winkler escalan lineal con la unidad
    assert mt.rmse(c * y, c * m) == pytest.approx(c * mt.rmse(y, m))
    assert mt.mean_winkler(c * y, c * lo, c * hi) == pytest.approx(c * mt.mean_winkler(y, lo, hi))
    wide = mt.mean_winkler(y, lo - 1e6, hi + 1e6)  # intervalo enorme: cubre todo, Winkler = ancho
    assert wide == pytest.approx(mt.mean_width(lo - 1e6, hi + 1e6))


def test_winkler_equals_width_when_all_covered() -> None:
    y = np.array([5.0, 6.0])
    assert mt.mean_winkler(y, y - 1, y + 2) == pytest.approx(3.0)
