"""El periodo reservado (holdout, desde 2026-08-01) no entra en la optimización de Optuna."""

import datetime as dt
from pathlib import Path

import optuna
import polars as pl
import pytest

from backtest_synth import synth_gold
from predico50hz.evaluation import folds as fl
from predico50hz.models import tuning

GOLD = Path(__file__).resolve().parents[2] / "data" / "gold" / "features"
HOLDOUT_START = dt.date(2026, 8, 1)
FAST = {"max_rounds": 10, "early_stopping_rounds": 3, "num_threads": 1}
FOLD_SETS = ["all", "even", "odd"]


def _tables() -> list[pl.DataFrame]:
    out = [synth_gold()]
    if (GOLD / "_delta_log").is_dir():
        out.append(pl.read_delta(str(GOLD)).select("target_date", "split_v01", "y_cf", "y_gen_mw"))
    return out


@pytest.mark.parametrize("which", FOLD_SETS)
def test_holdout_never_in_optimization_folds(which: str) -> None:
    for df in _tables():
        holdout = df.filter(pl.col("split_v01") == "holdout")["target_date"]
        assert holdout.min() == HOLDOUT_START
        for task in tuning.fold_tasks(df, which):
            for part in (task.train, task.valid):
                assert part["target_date"].max() < HOLDOUT_START
                assert set(part["split_v01"].unique().to_list()) <= {"train", "valid"}
            assert task.train["target_date"].max() < task.fold.valid_start
            assert task.fold.valid_end < HOLDOUT_START


def test_fold_tasks_refuse_holdout_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    df = synth_gold()
    holdout = df.filter(pl.col("split_v01") == "holdout")

    original = fl.split_fold

    def leaky(d: pl.DataFrame, f: fl.Fold) -> tuple[pl.DataFrame, pl.DataFrame]:
        train, valid = original(d, f)
        return pl.concat([train, holdout]), valid

    monkeypatch.setattr(tuning.fl, "split_fold", leaky)
    with pytest.raises(AssertionError, match="reservado"):
        tuning.fold_tasks(df)


def test_even_and_odd_cover_all_folds_once() -> None:
    for df in _tables():
        all_ = {t.fold.index for t in tuning.fold_tasks(df, "all")}
        even = {t.fold.index for t in tuning.fold_tasks(df, "even")}
        odd = {t.fold.index for t in tuning.fold_tasks(df, "odd")}
        assert even | odd == all_
        assert not even & odd


def test_objective_trains_before_validation_and_never_in_holdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Espía lo que ve LightGBM en cada intento: entrenamiento y parada temprana, sin futuro."""
    df = synth_gold()
    seen: list[tuple[dt.date, dt.date, dt.date]] = []
    original = tuning.split_early_stop

    def spy(train: pl.DataFrame, fraction: float, min_days: int):  # type: ignore[no-untyped-def]
        fit_df, es_df = original(train, fraction, min_days)
        seen.append((fit_df["target_date"].max(), es_df["target_date"].max(), HOLDOUT_START))
        return fit_df, es_df

    monkeypatch.setattr(tuning, "split_early_stop", spy)
    tasks = tuning.fold_tasks(df, "all")
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(tuning.make_objective(tasks, "q50", **FAST), n_trials=2)
    assert len(seen) == 2 * len(tasks)
    for i, (fit_max, es_max, holdout) in enumerate(seen):
        task = tasks[i % len(tasks)]
        assert fit_max < es_max < task.fold.valid_start
        assert es_max < holdout
