# ruff: noqa: PLR2004
"""LightGBM v0.1 con datos sintéticos: cuantiles, recorte, determinismo y parada temprana."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from backtest_synth import CAP, synth_gold, write_operator
from predico50hz.evaluation.backtest import finalize, run_backtest
from predico50hz.models import MODELS
from predico50hz.models.lgbm import LightGBM, feature_columns, split_early_stop

FAST = {"max_rounds": 30, "early_stopping_rounds": 5, "min_data_in_leaf": 20, "learning_rate": 0.2}


@pytest.fixture(scope="module")
def gold() -> pl.DataFrame:
    return synth_gold(dt.date(2025, 9, 2), dt.date(2026, 1, 31))


def _split(gold: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    train = gold.filter(pl.col("target_date") < dt.date(2026, 1, 1))
    valid = gold.filter(pl.col("target_date") >= dt.date(2026, 1, 1))
    return train, valid.drop([c for c in valid.columns if c.startswith("y_")])


def test_registered() -> None:
    assert MODELS["lgbm"] is LightGBM
    assert LightGBM.label


def test_feature_selection_excludes_meta_and_targets(gold: pl.DataFrame) -> None:
    cols = feature_columns(gold)
    assert set(cols) == {"month", "hour_local", "wshub_all_mean"}


def test_quantile_order_and_clipping_after_framework(gold: pl.DataFrame) -> None:
    train, valid = _split(gold)
    m = LightGBM(**FAST)
    m.fit(train)
    pred = m.predict(valid)
    assert pred.q10 is not None
    assert pred.q90 is not None
    cap = np.full(valid.height, CAP)
    q, _ = finalize(pred, m.unit, cap)
    assert q["q10"] is not None
    assert q["q90"] is not None
    assert (q["q10"] <= q["q50"]).all()
    assert (q["q50"] <= q["q90"]).all()
    for v in q.values():
        assert v is not None
        assert v.min() >= 0.0
        assert v.max() <= CAP


def test_learns_signal(gold: pl.DataFrame) -> None:
    train, valid = _split(gold)
    m = LightGBM(**FAST)
    m.fit(train)
    y = gold.filter(pl.col("target_date") >= dt.date(2026, 1, 1))["y_cf"].to_numpy()
    rmse = float(np.sqrt(np.mean((m.predict(valid).q50 - y) ** 2)))
    assert rmse < float(np.std(y)) * 0.5


def test_deterministic_with_same_seed(gold: pl.DataFrame) -> None:
    train, valid = _split(gold)
    a, b = LightGBM(**FAST, seed=7), LightGBM(**FAST, seed=7)
    a.fit(train)
    b.fit(train)
    pa, pb = a.predict(valid), b.predict(valid)
    assert np.array_equal(pa.q50, pb.q50)
    assert pa.q10 is not None
    assert pb.q10 is not None
    assert np.array_equal(pa.q10, pb.q10)


def test_params_contain_everything() -> None:
    p = LightGBM(**FAST).params()
    assert p["seed"] == 42
    assert p["deterministic"] is True
    assert p["objectives"]["q50"] == "regression"
    assert p["alphas"] == {"q10": 0.1, "q90": 0.9}


def test_early_stop_split_is_chronological_and_by_whole_days(gold: pl.DataFrame) -> None:
    train, _ = _split(gold)
    fit, es = split_early_stop(train, 0.15, 7)
    assert fit.height + es.height == train.height
    assert fit["target_date"].max() < es["target_date"].min()  # type: ignore[operator]
    assert set(fit["target_date"]).isdisjoint(set(es["target_date"]))
    # Mínimo de días.
    _, es_small = split_early_stop(train, 0.01, 7)
    assert es_small["target_date"].n_unique() == 7


def test_fit_does_not_touch_rows_after_train(gold: pl.DataFrame) -> None:
    train, _ = _split(gold)
    m = LightGBM(**FAST)
    m.fit(train)
    assert m.es_dates is not None
    assert m.es_dates[1] == train["target_date"].max()


def test_artifacts_and_run_directory(tmp_path: Path) -> None:
    g = synth_gold()
    op = tmp_path / "operator"
    write_operator(op, g)
    rec = run_backtest(
        "lgbm",
        root=tmp_path / "bt",
        operator=op,
        table=(g, 1),
        model_params=FAST,
    )
    imp = pl.read_parquet(rec.directory / "feature_importance.parquet")
    assert {"fold", "quantile", "feature", "gain"} <= set(imp.columns)
    assert imp["fold"].n_unique() == len(rec.run["folds"])
    es = pl.read_parquet(rec.directory / "lgbm_early_stopping.parquet")
    assert es.height == 3 * len(rec.run["folds"])
    p = rec.predictions
    assert (p["q10_mw"] <= p["q50_mw"]).all()
    assert (p["q50_mw"] <= p["q90_mw"]).all()
    assert p["q10_mw"].min() >= 0.0  # type: ignore[operator]
    assert (p["q90_mw"] <= p["cap_mw"]).all()
