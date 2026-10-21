# ruff: noqa: PLR2004, RUF003
"""CQR (`models/cqr.py`): cuantil con corrección finita, ajuste, mínimo y marco, a mano."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from backtest_synth import synth_gold, write_operator
from predico50hz.evaluation.backtest import run_backtest
from predico50hz.evaluation.validate import validate_predictions
from predico50hz.models import MODELS
from predico50hz.models.cqr import (
    CQRConfig,
    adjust_interval,
    calibrate_frame,
    conformal_quantile,
    conformal_scores,
    day_adjustments,
    last_usable_issue_date,
)
from predico50hz.models.lgbm import LightGBM, LightGBMCQR

D0 = dt.date(2026, 3, 1)
FAST = {"max_rounds": 20, "early_stopping_rounds": 5, "min_data_in_leaf": 20, "learning_rate": 0.2}


def test_scores_are_max_of_both_sides() -> None:
    e = conformal_scores(
        np.array([2.0, 2.0, 2.0]), np.array([5.0, 5.0, 5.0]), np.array([1, 3, 7.0])
    )
    # por debajo: 2 − 1 = 1; dentro: max(−1, −2) = −1; por encima: 7 − 5 = 2
    assert e.tolist() == [1.0, -1.0, 2.0]


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        (list(range(1, 11)), 9.0),  # n = 10: ceil(11 · 0,8) = ceil(8,8) = 9 → noveno menor
        ([4.0, 1.0, 3.0, 2.0], 4.0),  # n = 4: ceil(5 · 0,8) = 4 (sin error de coma flotante)
        ([float(i) for i in range(1, 20)], 16.0),  # n = 19: ceil(20 · 0,8) = 16
        ([-3.0, -2.0, -1.0, -0.5, -0.1], -0.1),  # n = 5: ceil(4,8) = 5 → el mayor, negativo
    ],
)
def test_conformal_quantile_finite_sample(scores: list[float], expected: float) -> None:
    assert conformal_quantile(np.array(scores)) == expected


def test_conformal_quantile_without_guarantee_is_none() -> None:
    # n = 3: ceil(4 · 0,8) = ceil(3,2) = 4 > 3; con n = 4 ya sale 4
    assert conformal_quantile(np.array([1.0, 2.0, 3.0])) is None
    assert conformal_quantile(np.array([])) is None


def test_adjust_is_symmetric_and_not_clipped() -> None:
    lo, hi = adjust_interval(np.array([40.0]), np.array([60.0]), 8.0)
    assert (lo[0], hi[0]) == (32.0, 68.0)
    lo, hi = adjust_interval(np.array([40.0]), np.array([60.0]), -5.0)  # estrecha
    assert (lo[0], hi[0]) == (45.0, 55.0)


@pytest.mark.parametrize(
    ("issue", "delay_h", "expected"),
    [
        (dt.date(2026, 3, 10), 6, dt.date(2026, 3, 8)),  # D = 10 → último objetivo 9 (cierre 8)
        (dt.date(2026, 3, 10), 0, dt.date(2026, 3, 8)),
        (dt.date(2026, 3, 10), 14, dt.date(2026, 3, 8)),  # 24:00 + 14 h < 15:00 (invierno)
        (dt.date(2026, 3, 10), 16, dt.date(2026, 3, 7)),  # con 16 h de retraso no llega
        (dt.date(2026, 3, 30), 6, dt.date(2026, 3, 28)),  # cambio de hora de por medio
        (dt.date(2026, 7, 10), 6, dt.date(2026, 7, 8)),
    ],
)
def test_last_usable_issue_date(issue: dt.date, delay_h: int, expected: dt.date) -> None:
    assert last_usable_issue_date(issue, dt.timedelta(hours=delay_h)) == expected


def _frame(n_days: int, rows: int = 2, cap: float = 100.0) -> pl.DataFrame:
    """Día i: intervalo [40, 60], q50 = 50, realidad 60 + 5·i → puntuación 5·i / 100 por fila."""
    recs: list[dict[str, object]] = []
    for i in range(n_days):
        d = D0 + dt.timedelta(days=i)
        for r in range(rows):
            recs.append(
                {
                    "forecast_issue_date": d,
                    "target_date": d + dt.timedelta(days=1),
                    "ts_utc": dt.datetime(2026, 3, 2, tzinfo=dt.UTC)
                    + dt.timedelta(days=i, minutes=15 * r),
                    "y_mw": 60.0 + 5.0 * i,
                    "cap_mw": cap,
                    "q10_mw": 40.0,
                    "q50_mw": 50.0,
                    "q90_mw": 60.0,
                }
            )
    return pl.DataFrame(recs)


def test_day_adjustments_by_hand() -> None:
    cfg = CQRConfig(window_days=5, min_days=3)
    adj = day_adjustments(_frame(10), cfg).sort("forecast_issue_date")
    by_day = {r["forecast_issue_date"]: r for r in adj.iter_rows(named=True)}
    # D = día 3: último utilizable día 1 → solo 2 días con residuos (< 3): no se ajusta
    r3 = by_day[D0 + dt.timedelta(days=3)]
    assert (r3["n_days"], r3["calibrated"], r3["qhat_cf"]) == (2, False, None)
    # D = día 4: días 0, 1, 2 → puntuaciones [0, 0, .05, .05, .10, .10], n = 6,
    # ceil(7 · 0,8) = 6 → el mayor: 0,10
    r4 = by_day[D0 + dt.timedelta(days=4)]
    assert (r4["n_days"], r4["n_residuals"], r4["calibrated"]) == (3, 6, True)
    assert r4["qhat_cf"] == pytest.approx(0.10)
    # D = día 9: último utilizable día 7, ventana de 5 días (3..7): [.15 .15 .20 .20 ... .35 .35],
    # n = 10, ceil(8,8) = 9 → noveno menor = 0,35
    r9 = by_day[D0 + dt.timedelta(days=9)]
    assert r9["window_start"] == D0 + dt.timedelta(days=3)
    assert r9["window_end"] == D0 + dt.timedelta(days=7)
    assert r9["qhat_cf"] == pytest.approx(0.35)


def test_minimum_days_leaves_frame_untouched() -> None:
    cfg = CQRConfig(window_days=5, min_days=3)
    out, adj = calibrate_frame(_frame(10), cfg)
    early = out.filter(pl.col("forecast_issue_date") <= D0 + dt.timedelta(days=3))
    assert early["q10_mw"].to_list() == [40.0] * early.height
    assert early["q90_mw"].to_list() == [60.0] * early.height
    assert adj.filter(~pl.col("calibrated")).height == 4  # días 0–3
    late = out.filter(pl.col("forecast_issue_date") == D0 + dt.timedelta(days=4))
    # Q̂ = 0,10 · capacidad 100 = 10 MW → [30, 70]
    assert late["q10_mw"].to_list() == [30.0, 30.0]
    assert late["q90_mw"].to_list() == [70.0, 70.0]
    assert late["q10_raw_mw"].to_list() == [40.0, 40.0]


def test_negative_qhat_narrows_and_keeps_order_and_bounds() -> None:
    # La realidad siempre cae en el centro: puntuación = max(40 − 50, 50 − 60) = −10 → −0,1
    preds = _frame(8).with_columns(
        pl.lit(50.0).alias("y_mw"),
        pl.when(pl.col("forecast_issue_date") == D0 + dt.timedelta(days=7))
        .then(45.0)  # q50 por debajo del q10 estrechado
        .otherwise(50.0)
        .alias("q50_mw"),
    )
    out, _ = calibrate_frame(preds, CQRConfig(window_days=5, min_days=3))
    day = out.filter(pl.col("forecast_issue_date") == D0 + dt.timedelta(days=7))
    # Q̂ = −0,1 → −10 MW: [50, 50]; con q50 = 45 el q10 baja hasta q50 y el q90 se queda en 50
    assert day["q10_mw"].to_list() == [45.0, 45.0]
    assert day["q90_mw"].to_list() == [50.0, 50.0]
    assert (out["q10_mw"] <= out["q50_mw"]).all()
    assert (out["q50_mw"] <= out["q90_mw"]).all()
    # y se recorta a [0, capacidad]
    wide, _ = calibrate_frame(
        _frame(8).with_columns(pl.lit(100.0).alias("y_mw"), pl.lit(10.0).alias("q10_mw")),
        CQRConfig(window_days=5, min_days=3),
    )
    assert wide["q10_mw"].min() >= 0.0
    assert wide["q90_mw"].max() <= 100.0


def test_registered_and_params() -> None:
    assert MODELS["lgbm_cqr"] is LightGBMCQR
    assert LightGBM().cqr_config() is None  # los modelos existentes no cambian
    m = LightGBMCQR(cqr_window_days=30, cqr_min_days=7, learning_rate=0.1)
    cfg = m.cqr_config()
    assert (cfg.window_days, cfg.min_days) == (30, 7)
    assert m.params()["cqr"]["window_days"] == 30
    assert m.params()["learning_rate"] == 0.1


def test_backtest_with_cqr_matches_plain_lgbm_raw_and_validates(tmp_path: Path) -> None:
    g = synth_gold()
    write_operator(tmp_path / "op", g)
    kw = {"root": tmp_path / "bt", "operator": tmp_path / "op", "table": (g, 1)}
    plain = run_backtest("lgbm", model_params=FAST, **kw)
    cal = run_backtest("lgbm_cqr", model_params={**FAST, "cqr_min_days": 5}, **kw)
    assert cal.run["cqr"]["window_days"] == 60
    assert cal.run["cqr"]["n_days_uncalibrated"] > 0
    raw = pl.read_parquet(cal.directory / "cqr_raw_intervals.parquet")
    adj = pl.read_parquet(cal.directory / "cqr_adjustment.parquet")
    assert adj.height == cal.predictions["forecast_issue_date"].n_unique()
    # el modelo es el mismo: los intervalos brutos son los de la ejecución sin calibrar
    both = raw.join(plain.predictions, on=["forecast_issue_date", "ts_utc"])
    assert both["q10_raw_mw"].to_list() == both["q10_mw_right"].to_list()
    assert both["q90_raw_mw"].to_list() == both["q90_mw_right"].to_list()
    assert cal.predictions["q50_mw"].to_list() == plain.predictions["q50_mw"].to_list()
    validate_predictions(cal.predictions, cal.predictions.select("forecast_issue_date", "ts_utc"))
    # sin Q̂ (primeros días) el intervalo no cambia
    first = adj.filter(~pl.col("calibrated"))["forecast_issue_date"]
    a = cal.predictions.filter(pl.col("forecast_issue_date").is_in(first.to_list()))
    b = plain.predictions.filter(pl.col("forecast_issue_date").is_in(first.to_list()))
    assert a["q10_mw"].to_list() == b["q10_mw"].to_list()


def test_cqr_model_refuses_holdout(tmp_path: Path) -> None:
    g = synth_gold()
    write_operator(tmp_path / "op", g)
    with pytest.raises(ValueError, match="calibración conformal"):
        run_backtest(
            "lgbm_cqr",
            holdout=True,
            root=tmp_path / "bt",
            operator=tmp_path / "op",
            table=(g, 1),
            model_params=FAST,
        )
