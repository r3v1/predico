"""Sumidero de MLflow: parámetros, métricas, etiquetas de versión, artefactos e idempotencia."""

from pathlib import Path

import polars as pl
import pytest

from predico50hz.evaluation.mlflow_sink import RUN_ID_TAG, MlflowRunSink
from predico50hz.evaluation.store import FileRunStore, RunRecord

mlflow = pytest.importorskip("mlflow")


def _record(tmp_path: Path) -> RunRecord:
    fold = {"fold": 0, "n_rows": 10, "rmse": 2.0, "mae": 1.5, "winkler": None}
    return RunRecord(
        run_id="20260101T000000Z-m-validation",
        model="m",
        mode="validation",
        params={"a": 1, "b": [1, 2]},
        run={
            "created_at": "2026-01-01T00:00:00+00:00",
            "gold": {"delta_version": 3},
            "nwp_snapshot": "SNAP123",
            "git_commit": "abc",
        },
        metrics={
            "total": {"rmse": 1.0, "n_rows": 20, "coverage": None},
            "by_fold": [fold, {**fold, "fold": 1, "rmse": 4.0}],
            "by_month": [],
        },
        predictions=pl.DataFrame({"x": [1, 2]}),
        directory=tmp_path / "runs" / "r1",
        tags={"mode": "validation", "gold_version": "3"},
    )


def test_registra_y_es_idempotente(tmp_path: Path) -> None:
    rec = _record(tmp_path)
    FileRunStore(tmp_path).save(rec)
    (rec.directory / "plot.png").write_bytes(b"png")
    sink = MlflowRunSink(f"file:{tmp_path / 'mlflow'}")

    assert sink.register(rec) is True
    assert sink.register(rec) is False  # no duplica
    sink.save(rec)

    exp = mlflow.get_experiment_by_name(sink.experiment)
    assert exp is not None
    runs = mlflow.search_runs([exp.experiment_id], output_format="list")
    assert len(runs) == 1
    r = runs[0]
    assert r.data.params == {"a": "1", "b": "[1, 2]"}
    assert r.data.metrics["rmse"] == 1.0
    assert "coverage" not in r.data.metrics
    assert r.data.tags[RUN_ID_TAG] == rec.run_id
    assert r.data.tags["gold_delta_version"] == "3"
    assert r.data.tags["nwp_icechunk_snapshot"] == "SNAP123"
    assert r.data.tags["git_commit"] == "abc"

    hist = mlflow.MlflowClient().get_metric_history(r.info.run_id, "fold_rmse")
    assert {(m.step, m.value) for m in hist} == {(0, 2.0), (1, 4.0)}
    arts = {a.path for a in mlflow.MlflowClient().list_artifacts(r.info.run_id)}
    assert {"predictions.parquet", "run.json", "metrics.json", "plot.png"} <= arts


def test_snapshot_desconocido_sin_repositorio(tmp_path: Path) -> None:
    rec = _record(tmp_path)
    rec.run.pop("nwp_snapshot")
    sink = MlflowRunSink(f"file:{tmp_path / 'mlflow'}", bronze=tmp_path / "no_existe")
    sink.register(rec)
    exp = mlflow.get_experiment_by_name(sink.experiment)
    assert exp is not None
    (r,) = mlflow.search_runs([exp.experiment_id], output_format="list")
    assert r.data.tags["nwp_icechunk_snapshot"] == "unknown"
