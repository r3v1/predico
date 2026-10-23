# ruff: noqa: RUF003, PLR2004
"""Marco de backtest de extremo a extremo con datos sintéticos (sin red ni `data/`)."""

import datetime as dt
import json
from pathlib import Path
from typing import ClassVar

import numpy as np
import polars as pl
import pytest

from backtest_synth import CAP, synth_gold, write_operator
from predico50hz.evaluation import scorecard
from predico50hz.evaluation.backtest import finalize, run_backtest
from predico50hz.evaluation.store import build_leaderboard, holdout_count, holdout_log, load_runs
from predico50hz.evaluation.validate import PredictionsInvalidError, validate_predictions
from predico50hz.models import MODELS
from predico50hz.models.base import Model, Prediction
from predico50hz.models.baselines import Climatology, Operator, PowerCurve


@pytest.fixture
def env(tmp_path: Path) -> dict:
    gold = synth_gold()
    op = tmp_path / "operator"
    write_operator(op, gold)
    return {"gold": gold, "op": op, "root": tmp_path / "bt"}


def _run(env: dict, name: str, **kw):
    return run_backtest(
        name,
        root=env["root"],
        operator=env["op"],
        table=(env["gold"], 3),
        min_train_months=3,  # los datos sintéticos son de 13 meses
        **kw,
    )


def test_finalize_orders_clips_and_converts() -> None:
    cap = np.array([100.0, 100.0, 100.0])
    pred = Prediction(
        q50=np.array([0.5, 1.2, -0.1]),
        q10=np.array([0.6, 0.9, -0.3]),
        q90=np.array([0.4, 1.5, 0.0]),
    )
    q, changed = finalize(pred, "cf", cap)
    assert q["q50"].tolist() == [50.0, 100.0, 0.0]  # 1,2 y −0,1 recortados
    assert q["q10"].tolist() == [50.0, 90.0, 0.0]  # 0,6 > q50 → baja hasta q50
    assert q["q90"].tolist() == [50.0, 100.0, 0.0]  # 0,4 < q50 → sube hasta q50
    assert changed > 0
    q2, _ = finalize(Prediction(q50=np.array([5.0])), "mw", np.array([10.0]))
    assert q2["q10"] is None
    assert q2["q50"].tolist() == [5.0]


def test_prediction_requires_both_or_none() -> None:
    with pytest.raises(ValueError, match="juntos"):
        Prediction(q50=np.zeros(2), q10=np.zeros(2))


def test_run_validation_files_and_metrics(env) -> None:
    rec = _run(env, "power_curve")
    d = Path(rec.directory)
    assert {p.name for p in d.iterdir()} == {"predictions.parquet", "metrics.json", "run.json"}
    run = json.loads((d / "run.json").read_text())
    assert run["model"] == "power_curve"
    assert run["mode"] == "validation"
    assert run["gold"]["delta_version"] == 3
    assert run["lockfile_sha256"] is None or len(run["lockfile_sha256"]) == 64
    assert "git_commit" in run
    assert run["params"]["feature"] == "wshub_all_mean"
    met = json.loads((d / "metrics.json").read_text())
    assert set(met) == {"total", "by_fold", "by_month"}
    assert len(met["by_fold"]) == len(run["folds"]) == 8
    assert met["total"]["coverage"] is not None
    assert met["total"]["rmse"] < met["total"]["rmse_operator"] * 3
    preds = pl.read_parquet(d / "predictions.parquet")
    # el holdout no aparece ni en predicciones ni en pliegues
    assert preds["target_date"].max() <= dt.date(2026, 7, 31)
    assert preds["fold"].min() == 0
    # q10 ≤ q50 ≤ q90 y 0 ≤ q ≤ capacidad
    assert (preds["q10_mw"] <= preds["q50_mw"]).all()
    assert (preds["q50_mw"] <= preds["q90_mw"]).all()
    assert (preds["q90_mw"] <= preds["cap_mw"]).all()
    assert (preds["q10_mw"] >= 0).all()
    assert holdout_count(env["root"]) == 0


def test_operator_run_has_no_interval_metrics(env) -> None:
    rec = _run(env, "operator")
    t = rec.metrics["total"]
    assert t["winkler"] is None
    assert t["coverage"] is None
    assert t["days_better_than_operator_pct"] is None
    assert t["rmse"] == pytest.approx(t["rmse_operator"])  # sin recorte relevante


def test_holdout_is_explicit_and_logged(env) -> None:
    rec = _run(env, "climatology", holdout=True)
    assert rec.mode == "holdout"
    assert rec.predictions["target_date"].min() >= dt.date(2026, 8, 1)
    assert rec.run["folds"][0]["fold"] == -1
    assert rec.run["holdout_evaluations_before"] == 0
    _run(env, "climatology", holdout=True)
    _run(env, "power_curve", holdout=True)
    log = holdout_log(env["root"])
    assert [e["model"] for e in log] == ["climatology", "climatology", "power_curve"]
    assert holdout_count(env["root"], "climatology") == 2
    assert holdout_count(env["root"]) == 3
    assert log[1]["evaluations_before_for_model"] == 1
    assert {"at", "user", "run_id", "gold_version"} <= set(log[0])


def test_validation_run_never_touches_holdout_log(env) -> None:
    _run(env, "climatology")
    assert not (Path(env["root"]) / "holdout_log.jsonl").exists()


class _Probe(Model):
    """Comprueba que `predict` no recibe columnas `y_*` y que `fit` sí solo ve el pasado."""

    name: ClassVar[str] = "probe"
    seen_train_max: ClassVar[list[dt.date]] = []

    def fit(self, train: pl.DataFrame) -> None:
        self._max = train["target_date"].max()
        _Probe.seen_train_max.append(self._max)

    def predict(self, df: pl.DataFrame) -> Prediction:
        assert not [c for c in df.columns if c.startswith("y_")]
        assert df["target_date"].min() > self._max
        return Prediction(q50=np.full(df.height, 0.3))


def test_framework_hides_targets_and_trains_on_past(env, monkeypatch) -> None:
    monkeypatch.setitem(MODELS, "probe", _Probe)
    _Probe.seen_train_max.clear()
    rec = _run(env, "probe")
    assert len(_Probe.seen_train_max) == 8
    assert rec.metrics["total"]["coverage"] is None
    assert (rec.predictions["q50_mw"] == 0.3 * CAP).all()


def test_leaderboard_lists_all_runs_and_latest(env) -> None:
    _run(env, "climatology")
    _run(env, "climatology")
    _run(env, "power_curve")
    lb = build_leaderboard(env["root"])
    assert lb.height == 3
    assert lb.filter(pl.col("is_latest")).height == 2
    assert (Path(env["root"]) / "leaderboard.parquet").is_file()
    assert len(json.loads((Path(env["root"]) / "leaderboard.json").read_text())) == 3
    assert set(lb["rank_rmse"]) <= {1, 2, 3}


def test_unknown_model_fails(env) -> None:
    with pytest.raises(KeyError, match="desconocido"):
        _run(env, "no_existe")


def _preds(**over) -> pl.DataFrame:
    ts = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(minutes=15 * i) for i in range(3)]
    d = dt.date(2026, 1, 1)
    base = {
        "forecast_issue_date": [d] * 3,
        "target_date": [d] * 3,
        "ts_utc": ts,
        "fold": [0] * 3,
        "n_steps_day": [3] * 3,
        "y_mw": [1.0, 2.0, 3.0],
        "cap_mw": [10.0] * 3,
        "q10_mw": [0.0, 1.0, 2.0],
        "q50_mw": [1.0, 2.0, 3.0],
        "q90_mw": [2.0, 3.0, 4.0],
        "op_q50_mw": [1.0] * 3,
    }
    return pl.DataFrame({**base, **over}).with_columns(pl.col("ts_utc").dt.cast_time_unit("us"))


def test_validation_accepts_good_and_rejects_bad() -> None:
    good = _preds()
    validate_predictions(good, good)
    with pytest.raises(PredictionsInvalidError, match="q10"):
        validate_predictions(_preds(q10_mw=[0.0, 2.5, 2.0]))
    with pytest.raises(PredictionsInvalidError, match="capacidad"):
        validate_predictions(_preds(q90_mw=[2.0, 3.0, 11.0]))
    with pytest.raises(PredictionsInvalidError, match="capacidad"):
        validate_predictions(_preds(q10_mw=[-1.0, 1.0, 2.0]))
    with pytest.raises(PredictionsInvalidError, match="malla"):
        validate_predictions(good.head(2), good)  # falta una fila de la malla
    dup = pl.concat([good, good.head(1)])
    with pytest.raises(PredictionsInvalidError):
        validate_predictions(dup)
    gap = good.filter(pl.col("y_mw") != 2.0)
    with pytest.raises(PredictionsInvalidError, match="saltos"):
        validate_predictions(gap)
    with pytest.raises(PredictionsInvalidError, match="NaN"):
        validate_predictions(_preds(q50_mw=[1.0, float("nan"), 3.0]))
    with pytest.raises(PredictionsInvalidError):
        validate_predictions(_preds(q10_mw=[None, None, None]))  # q90 presente y q10 no


def test_improvement_and_verdict_threshold() -> None:
    assert scorecard.improvement_pct(1000.0, 900.0) == pytest.approx(10.0)
    assert scorecard.improvement_pct(1000.0, 1100.0) == pytest.approx(-10.0)
    assert scorecard.improvement_pct(None, 1.0) is None
    assert scorecard.verdict(10.0) == "mejora"
    assert scorecard.verdict(1.01) == "mejora"
    assert scorecard.verdict(1.0) == "igual"  # el umbral exacto cuenta como «igual»
    assert scorecard.verdict(-0.5) == "igual"
    assert scorecard.verdict(-1.0) == "igual"
    assert scorecard.verdict(-1.01) == "empeora"
    assert scorecard.verdict(None) is None


def test_scorecard_content(env) -> None:
    _run(env, "operator")
    _run(env, "climatology")
    _run(env, "power_curve")
    _run(env, "power_curve")
    _run(env, "power_curve", holdout=True)
    card = scorecard.build_scorecard(env["root"])
    by = {m["model"]: m for m in card["models"]}
    assert set(by) == {"operator", "climatology", "power_curve"}
    pc = by["power_curve"]
    assert pc["validation"]["verdict"] in {"mejora", "empeora", "igual"}
    assert pc["validation"]["vs_operator_pct"] == pytest.approx(
        (pc["validation"]["rmse_operator"] - pc["validation"]["rmse"])
        / pc["validation"]["rmse_operator"]
        * 100
    )
    assert pc["trend"]["direction"] == "igual"  # misma ejecución repetida
    assert pc["trend"]["previous_run_id"] is not None
    assert by["climatology"]["trend"]["direction"] == "primera ejecución"
    assert pc["holdout"] is not None
    assert pc["holdout_evaluations"] == 1
    assert by["climatology"]["holdout"] is None
    assert by["climatology"]["g2"]["status"] == "sin evaluar"
    assert by["operator"]["g1"]["status"] == "no aplica"
    assert pc["g1"]["status"] in {"cumple", "no cumple"}
    assert pc["g1"]["reasons"]
    assert card["headline"]["best_model"] in {"power_curve", "climatology"}
    assert card["headline"]["text"]
    assert card["rules"]["verdict_threshold_pct"] == 1.0
    assert card["holdout_evaluations_total"] == 1
    assert len(card["history"]) == 5
    json.dumps(card, allow_nan=False)  # serializable, sin NaN


def test_g1_rules() -> None:
    ok = {"rmse": 90.0, "rmse_operator": 100.0, "coverage": 0.8}
    assert scorecard._g1(ok, False)["status"] == "cumple"
    assert scorecard._g1({**ok, "rmse": 100.0}, False)["status"] == "cumple"  # ≤
    assert scorecard._g1({**ok, "rmse": 100.1}, False)["status"] == "no cumple"
    assert scorecard._g1({**ok, "coverage": 0.755}, False)["status"] == "no cumple"
    assert scorecard._g1({**ok, "coverage": 0.76}, False)["status"] == "cumple"
    assert scorecard._g1({**ok, "coverage": 0.84}, False)["status"] == "cumple"
    assert scorecard._g1({**ok, "coverage": 0.841}, False)["status"] == "no cumple"
    assert scorecard._g1({**ok, "coverage": None}, False)["status"] == "no cumple"


def test_baselines_basic() -> None:
    df = synth_gold()
    train = df.filter(pl.col("split_v01") == "train")
    for m in (Climatology(), PowerCurve()):
        m.fit(train)
        p = m.predict(df.head(50).drop([c for c in df.columns if c.startswith("y_")]))
        assert p.q10 is not None
        assert p.q90 is not None
        assert np.isfinite(p.q50).all()
    # la curva de potencia es creciente con el viento en el sintético
    pc = PowerCurve()
    pc.fit(train)
    probe = pl.DataFrame({"wshub_all_mean": [3.0, 8.0, 13.0]})
    q = pc.predict(probe).q50
    assert q[0] < q[1] < q[2]
    assert Operator.is_operator
    assert load_runs(Path("/nonexistent")) == []
