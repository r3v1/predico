"""Sumidero de MLflow para el backtest: un run por `run_id`, idempotente.

Backend local: almacén de ficheros `file:` bajo `data/mlflow/` (`mlflow-skinny` no trae SQLAlchemy,
así que `sqlite:` no está disponible). MLflow 3.17 lo marca «en mantenimiento» y exige
`MLFLOW_ALLOW_FILE_STORE=true`, que se fija aquí. `mlflow` se importa de forma perezosa.

Cada run lleva las etiquetas de versión de datos: `gold_delta_version` (Delta de la tabla oro) y
`nwp_icechunk_snapshot` (instantánea del bronce NWP; ver `nwp_snapshot`).
"""

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from predico50hz.evaluation.store import (
    DEFAULT_ROOT,
    LEADER_FLOATS,
    RunRecord,
    load_runs,
)
from predico50hz.ingest.dynamical.config import DEFAULT_BRONZE

DEFAULT_TRACKING_URI = "file:data/mlflow"
DEFAULT_EXPERIMENT = "predico50hz-backtest"
RUN_ID_TAG = "predico.run_id"
UNKNOWN = "unknown"
FOLD_METRICS = (*LEADER_FLOATS, "n_rows", "n_days")


def nwp_snapshot(created_at: str, bronze: str | Path = DEFAULT_BRONZE) -> str:
    """Instantánea de `main` del Icechunk NWP vigente en `created_at` (ISO), o `unknown`.

    El bronce solo crece, así que la última instantánea escrita antes de la ejecución es la que
    pudo leer. Si el repositorio no es accesible o no hay ninguna anterior, devuelve `unknown`.
    """
    try:
        import icechunk  # noqa: PLC0415

        repo = icechunk.Repository.open(icechunk.local_filesystem_storage(str(bronze)))
        when = dt.datetime.fromisoformat(created_at)
        for snap in repo.ancestry(branch="main"):  # de la más reciente a la más antigua
            if snap.written_at <= when:
                return str(snap.id)
    except Exception:
        pass
    return UNKNOWN


class MlflowRunSink:
    """Guarda cada `RunRecord` como un run de MLflow; no duplica un `run_id` ya registrado."""

    def __init__(
        self,
        tracking_uri: str = DEFAULT_TRACKING_URI,
        experiment: str = DEFAULT_EXPERIMENT,
        bronze: str | Path = DEFAULT_BRONZE,
    ) -> None:
        self.tracking_uri = tracking_uri
        self.experiment = experiment
        self.bronze = bronze

    def _client(self) -> Any:
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        import mlflow  # noqa: PLC0415

        mlflow.set_tracking_uri(self.tracking_uri)
        return mlflow

    def save(self, record: RunRecord) -> None:
        """Interfaz `RunSink`: registra la ejecución si aún no existe."""
        self.register(record)

    def register(self, record: RunRecord) -> bool:
        """Registra la ejecución; devuelve `False` si ya existía (no se toca)."""
        mlflow = self._client()
        exp = mlflow.set_experiment(self.experiment)
        found = mlflow.search_runs(
            [exp.experiment_id],
            filter_string=f"tags.`{RUN_ID_TAG}` = '{record.run_id}'",
            output_format="list",
        )
        if found:
            return False
        created = str(record.run.get("created_at", ""))
        snapshot = str(
            record.run.get("nwp_snapshot")
            or (nwp_snapshot(created, self.bronze) if created else UNKNOWN)
        )
        tags = {
            **record.tags,
            RUN_ID_TAG: record.run_id,
            "model": record.model,
            "mode": record.mode,
            "gold_delta_version": str(record.run.get("gold", {}).get("delta_version", UNKNOWN)),
            "nwp_icechunk_snapshot": snapshot,
            "git_commit": str(record.run.get("git_commit") or UNKNOWN),
            "lockfile_sha256": str(record.run.get("lockfile_sha256") or UNKNOWN),
        }
        with mlflow.start_run(run_name=record.run_id, tags=tags):
            mlflow.log_params(
                {k: json.dumps(v, ensure_ascii=False) for k, v in record.params.items()}
            )
            mlflow.log_metrics(record.flat_metrics)
            for f in record.metrics.get("by_fold", []):
                for k in FOLD_METRICS:
                    v = f.get(k)
                    if isinstance(v, int | float) and not isinstance(v, bool):
                        mlflow.log_metric(f"fold_{k}", float(v), step=int(f["fold"]))
            if record.directory.is_dir():
                mlflow.log_artifacts(str(record.directory))
            if not (record.directory / "predictions.parquet").is_file():
                with tempfile.TemporaryDirectory() as tmp:
                    record.predictions.write_parquet(Path(tmp) / "predictions.parquet")
                    mlflow.log_artifact(str(Path(tmp) / "predictions.parquet"))
        return True


def record_from_disk(run: dict[str, Any]) -> RunRecord:
    """Reconstruye el `RunRecord` de una ejecución (entrada de `store.load_runs`)."""
    d = Path(run["directory"])
    run_json = json.loads((d / "run.json").read_text("utf-8"))
    return RunRecord(
        run_id=run_json["run_id"],
        model=run_json["model"],
        mode=run_json["mode"],
        params=run_json["params"],
        run=run_json,
        metrics=run["metrics"],
        predictions=pl.read_parquet(d / "predictions.parquet"),
        directory=d,
        tags={"mode": run_json["mode"], "gold_version": str(run_json["gold"]["delta_version"])},
    )


def sync(root: str | Path = DEFAULT_ROOT, sink: MlflowRunSink | None = None) -> tuple[int, int]:
    """Registra en MLflow las ejecuciones de disco; devuelve (nuevas, ya existentes)."""
    sink = sink or MlflowRunSink()
    new = 0
    old = 0
    for run in load_runs(root):
        if sink.register(record_from_disk(run)):
            new += 1
        else:
            old += 1
    return new, old
