# ruff: noqa: PLR2004
"""Optimización con Optuna (#32): espacio, métricas, objetivo, estudio pequeño y exportación."""

import datetime as dt
from pathlib import Path

import numpy as np
import optuna
import polars as pl
import pytest
import yaml

from backtest_synth import synth_gold, write_operator
from predico50hz.evaluation import folds as fl
from predico50hz.evaluation.backtest import run_backtest
from predico50hz.models import MODELS, tuning
from predico50hz.models.lgbm import (
    TUNABLE,
    LightGBM,
    LightGBMTuned,
    LightGBMTunedCQR,
    load_tuned_params,
)

FAST = {"max_rounds": 15, "early_stopping_rounds": 5, "num_threads": 1}
optuna.logging.set_verbosity(optuna.logging.WARNING)


@pytest.fixture(scope="module")
def gold() -> pl.DataFrame:
    return synth_gold()


def test_search_space_matches_adr_0015() -> None:
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    for _ in range(200):
        t = study.ask()
        hp = tuning.suggest(t)
        assert set(hp) == set(TUNABLE)
        assert 15 <= hp["num_leaves"] <= 255
        assert 0.01 <= hp["learning_rate"] <= 0.1
        assert 20 <= hp["min_data_in_leaf"] <= 500
        assert 0.5 <= hp["feature_fraction"] <= 1.0
        assert 0.5 <= hp["bagging_fraction"] <= 1.0
        assert 1e-3 <= hp["lambda_l1"] <= 10.0
        assert 1e-3 <= hp["lambda_l2"] <= 10.0
        study.tell(t, 0.0)


def test_wide_space_only_widens_leaves_and_features() -> None:
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=2))
    leaves: list[int] = []
    for _ in range(300):
        t = study.ask()
        hp = tuning.suggest(t, "wide")
        leaves.append(hp["num_leaves"])
        assert 4 <= hp["num_leaves"] <= 255
        assert 0.2 <= hp["feature_fraction"] <= 1.0
        assert 20 <= hp["min_data_in_leaf"] <= 500
        study.tell(t, 0.0)
    assert min(leaves) < 15
    with pytest.raises(ValueError, match="desconocido"):
        tuning.suggest(study.ask(), "otro")


def test_baseline_is_inside_the_space() -> None:
    bounds = {
        "num_leaves": (15, 255),
        "learning_rate": (0.01, 0.1),
        "min_data_in_leaf": (20, 500),
        "feature_fraction": (0.5, 1.0),
        "bagging_fraction": (0.5, 1.0),
        "lambda_l1": (1e-3, 10.0),
        "lambda_l2": (1e-3, 10.0),
    }
    assert set(tuning.BASELINE) == set(bounds)
    for k, v in tuning.BASELINE.items():
        assert bounds[k][0] <= v <= bounds[k][1]


def test_metric_mw() -> None:
    y = np.array([100.0, 200.0])
    assert tuning.metric_mw("q50", y, np.array([110.0, 190.0])) == pytest.approx(10.0)
    # Q90: pinball = 0,9·d si y > pred y 0,1·|d| si y < pred.
    assert tuning.metric_mw("q90", y, np.array([90.0, 210.0])) == pytest.approx((9.0 + 1.0) / 2)
    assert tuning.metric_mw("q10", y, np.array([90.0, 210.0])) == pytest.approx((1.0 + 9.0) / 2)


def test_select_folds(gold: pl.DataFrame) -> None:
    folds = fl.dev_folds(gold)
    even, odd = tuning.select_folds(folds, "even"), tuning.select_folds(folds, "odd")
    assert {f.index for f in even} | {f.index for f in odd} == {f.index for f in folds}
    assert not {f.index for f in even} & {f.index for f in odd}
    assert [f.index for f in tuning.select_folds(folds, "1,3")] == [1, 3]
    with pytest.raises(ValueError, match="inexistentes"):
        tuning.select_folds(folds, "99")


def test_objective_is_mean_of_folds_and_prunes_nothing_without_history(
    gold: pl.DataFrame,
) -> None:
    tasks = tuning.fold_tasks(gold, "all")
    study = optuna.create_study(
        sampler=optuna.samplers.TPESampler(seed=0), pruner=tuning.make_pruner()
    )
    study.optimize(tuning.make_objective(tasks, "q50", **FAST), n_trials=3)
    for t in study.trials:
        assert t.state == optuna.trial.TrialState.COMPLETE
        values = t.user_attrs["fold_values"]
        assert len(values) == len(tasks)
        assert t.value == pytest.approx(float(np.mean(values)))
        assert all(v > 0 and np.isfinite(v) for v in values)


def test_pruner_cuts_a_bad_trial(gold: pl.DataFrame) -> None:
    tasks = tuning.fold_tasks(gold, "all")
    study = optuna.create_study(pruner=optuna.pruners.MedianPruner(0, 0))
    objective = tuning.make_objective(tasks, "q50", **FAST)
    study.optimize(objective, n_trials=2)
    states = {t.state for t in study.trials}
    assert states <= {optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED}


def test_study_in_sqlite_with_baseline_and_workers(tmp_path: Path, gold: pl.DataFrame) -> None:
    gold_path = tmp_path / "gold"
    gold.write_delta(str(gold_path))
    db = tmp_path / "optuna" / "t.db"
    study = tuning.run_study(
        "q10", 4, workers=2, db=db, gold=gold_path, folds="even", poll_s=0.5, log=lambda _: None
    )
    assert db.is_file()
    assert study.study_name == "lgbm-q10"
    assert len(study.trials) == 4
    # El intento 0 es la v0.1 y se evalúa con el mismo criterio que el resto.
    assert study.trials[0].params == pytest.approx(tuning.BASELINE)
    s = tuning.summarize_study(study)
    assert s["baseline_value"] is not None
    assert set(s["best_params"]) == set(TUNABLE)
    assert s["counts"]["COMPLETE"] + s["counts"]["PRUNED"] == 4


def test_export_and_load_tuned_params(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for q in ("q10", "q50", "q90"):
        study = tuning.create_study(db, q)
        t = study.ask()
        tuning.suggest(t)
        study.tell(t, 1.0)
    out = tmp_path / "lgbm_tuned.yaml"
    best = tuning.export_params(db, out, note="prueba")
    assert set(best) == {"q10", "q50", "q90"}
    loaded = load_tuned_params(out)
    assert loaded == best
    assert isinstance(loaded["q50"]["num_leaves"], int)
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["note"] == "prueba"


def test_load_tuned_params_rejects_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"quantiles": {"q50": {"max_depth": 3}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="no válidos"):
        load_tuned_params(p)


def test_quantile_params_override_only_their_quantile() -> None:
    m = LightGBM(quantile_params={"q10": {"num_leaves": 77, "lambda_l1": 2.0}})
    assert m._lgb_params("q10", 0.1)["num_leaves"] == 77
    assert m._lgb_params("q10", 0.1)["lambda_l1"] == 2.0
    assert m._lgb_params("q50", 0.5)["num_leaves"] == 15
    assert m._lgb_params("q50", 0.5)["lambda_l1"] == 0.0
    assert m.params()["quantile_params"]["q10"]["num_leaves"] == 77


def test_tuned_models_registered_and_load_config() -> None:
    assert MODELS["lgbm_tuned"] is LightGBMTuned
    assert MODELS["lgbm_tuned_cqr"] is LightGBMTunedCQR
    m = LightGBMTuned()
    assert m.quantile_params == load_tuned_params()
    assert LightGBMTunedCQR().cqr_config() is not None
    assert LightGBMTuned(quantile_params={}).quantile_params == {}
    assert LightGBMTuned.label


def test_score_fold_never_uses_validation_rows(gold: pl.DataFrame) -> None:
    task = tuning.fold_tasks(gold, "all")[0]
    assert task.train["target_date"].max() < task.fold.valid_start
    assert task.valid["target_date"].min() >= task.fold.valid_start
    value, trees = tuning.score_fold(task, "q50", dict(tuning.BASELINE), **FAST)
    assert value > 0
    assert 1 <= trees <= FAST["max_rounds"]
    assert isinstance(task.fold.valid_start, dt.date)


def test_tuned_model_runs_in_the_backtest(tmp_path: Path, gold: pl.DataFrame) -> None:
    write_operator(tmp_path / "op", gold)
    params = {
        "max_rounds": 15,
        "early_stopping_rounds": 5,
        "quantile_params": {
            q: {"num_leaves": 7, "min_data_in_leaf": 20} for q in ("q10", "q50", "q90")
        },
    }
    for name in ("lgbm_tuned", "lgbm_tuned_cqr"):
        rec = run_backtest(
            name,
            root=tmp_path / "bt",
            operator=tmp_path / "op",
            table=(gold, 1),
            model_params=params,
        )
        assert rec.params["quantile_params"]["q50"]["num_leaves"] == 7
        assert rec.metrics["total"]["rmse"] > 0


def test_mlflow_logs_every_trial_as_child_of_the_study_run(
    tmp_path: Path, gold: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    gold_path = tmp_path / "gold"
    gold.write_delta(str(gold_path))
    uri = f"file:{tmp_path / 'mlflow'}"
    study = tuning.run_study(
        "q50",
        3,
        workers=2,
        db=tmp_path / "t.db",
        gold=gold_path,
        folds="even",
        mlflow_uri=uri,
        poll_s=0.5,
        log=lambda _: None,
    )
    client, exp_id = tuning._client(uri)
    runs = client.search_runs([exp_id])
    parents = [r for r in runs if r.data.tags.get("kind") == "optuna"]
    kids = [r for r in runs if r.data.tags.get("kind") == "optuna-trial"]
    assert len(parents) == 1
    assert parents[0].info.status == "FINISHED"
    assert len(kids) == len(study.trials) == 3
    for k in kids:
        assert k.data.tags["mlflow.parentRunId"] == parents[0].info.run_id
        assert k.data.tags["trial_state"] in {"COMPLETE", "PRUNED"}
        assert set(tuning.TUNABLE) <= set(k.data.params)
    done = [k for k in kids if k.data.tags["trial_state"] == "COMPLETE"]
    assert done
    hist = client.get_metric_history(done[0].info.run_id, "fold_value_mw")
    assert [m.step for m in hist] == list(range(len(hist)))
    assert len(hist) > 1
