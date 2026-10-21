"""Registro de ejecuciones del backtest en disco (`data/backtest/`).

`RunRecord` es el contenido de una ejecución (parámetros, métricas planas, etiquetas) y
`RunSink` la interfaz mínima para guardarla. `FileRunStore` escribe `run.json`, `metrics.json` y
`predictions.parquet` bajo `runs/<run_id>/`.

`evaluation/mlflow_sink.py` aporta `MlflowRunSink` (sumidero opcional hacia MLflow, ver
`docs/f4-backtest.md` §7); se añade a `backtest.run_backtest(sinks=[...])` o con `run --mlflow`.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import polars as pl

DEFAULT_ROOT = "data/backtest"
RUNS_DIR = "runs"
HOLDOUT_LOG = "holdout_log.jsonl"
LEADERBOARD = "leaderboard"
MODES = ("validation", "holdout")
LEADER_FLOATS = (
    "rmse",
    "mae",
    "bias",
    "winkler",
    "coverage",
    "mean_width",
    "rmse_operator",
    "peak_abs_error_mw",
    "peak_bias_mw",
    "peak_shift_h",
    "days_better_than_operator_pct",
)


@dataclass
class RunRecord:
    """Una ejecución completa, lista para guardar en cualquier sumidero."""

    run_id: str
    model: str
    mode: str
    params: dict[str, Any]
    run: dict[str, Any]  # contenido de run.json
    metrics: dict[str, Any]  # contenido de metrics.json: total, by_fold, by_month
    predictions: pl.DataFrame
    directory: Path
    tags: dict[str, str] = field(default_factory=lambda: {})

    @property
    def flat_metrics(self) -> dict[str, float]:
        """Métricas totales numéricas y no nulas (formato `mlflow.log_metrics`)."""
        return {
            k: float(v)
            for k, v in self.metrics["total"].items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        }


class RunSink(Protocol):
    def save(self, record: RunRecord) -> None: ...


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


class FileRunStore:
    """Sumidero por defecto: carpeta por ejecución en `<root>/runs/<run_id>/`."""

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def save(self, record: RunRecord) -> None:
        d = record.directory
        d.mkdir(parents=True, exist_ok=True)
        record.predictions.write_parquet(d / "predictions.parquet")
        _dump(d / "metrics.json", record.metrics)
        _dump(d / "run.json", record.run)


def runs_root(root: str | Path) -> Path:
    return Path(root) / RUNS_DIR


def load_runs(root: str | Path) -> list[dict[str, Any]]:
    """`run.json` + métricas totales de todas las ejecuciones, por fecha de creación."""
    out: list[dict[str, Any]] = []
    for d in sorted(runs_root(root).glob("*/run.json")):
        run = json.loads(d.read_text("utf-8"))
        met = json.loads((d.parent / "metrics.json").read_text("utf-8"))
        out.append({**run, "metrics": met, "directory": str(d.parent)})
    out.sort(key=lambda r: (r["created_at"], r["run_id"]))
    return out


def holdout_log(root: str | Path) -> list[dict[str, Any]]:
    p = Path(root) / HOLDOUT_LOG
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]


def holdout_count(root: str | Path, model: str | None = None) -> int:
    """Veces que se ha evaluado el periodo reservado (en total o para un modelo)."""
    return sum(1 for e in holdout_log(root) if model is None or e["model"] == model)


def log_holdout(root: str | Path, entry: dict[str, Any]) -> None:
    p = Path(root) / HOLDOUT_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")


def build_leaderboard(root: str | Path) -> pl.DataFrame:
    """Reescribe `leaderboard.parquet` y `.json` con todas las ejecuciones y lo devuelve."""
    rows: list[dict[str, Any]] = []
    for r in load_runs(root):
        tot = r["metrics"]["total"]
        rows.append(
            {
                "run_id": r["run_id"],
                "model": r["model"],
                "mode": r["mode"],
                "created_at": r["created_at"],
                "gold_version": r["gold"]["delta_version"],
                "git_commit": r.get("git_commit"),
                "n_rows": int(tot.get("n_rows", 0)),
                "n_days": int(tot.get("n_days", 0)),
                **{k: tot.get(k) for k in LEADER_FLOATS},
            }
        )
    schema: dict[str, Any] = {
        "run_id": pl.String,
        "model": pl.String,
        "mode": pl.String,
        "created_at": pl.String,
        "gold_version": pl.Int64,
        "git_commit": pl.String,
        "n_rows": pl.Int64,
        "n_days": pl.Int64,
        **dict.fromkeys(LEADER_FLOATS, pl.Float64),
    }
    df = pl.DataFrame(rows, schema=schema)
    df = df.with_columns(
        pl.col("rmse").rank("min").over("mode").cast(pl.Int64).alias("rank_rmse"),
        (pl.col("run_id") == pl.col("run_id").last().over("model", "mode")).alias("is_latest"),
    )
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    df.write_parquet(base / f"{LEADERBOARD}.parquet")
    _dump(base / f"{LEADERBOARD}.json", df.to_dicts())
    return df


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
