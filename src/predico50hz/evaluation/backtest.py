"""Ejecución del backtest walk-forward (F4.1) y evaluación del periodo reservado (F4.2).

Flujo de `run_backtest`: carga la tabla oro → por pliegue entrena un modelo nuevo con la
ventana creciente y pronostica el mes siguiente → el marco convierte a MW con `y_cap_mw`, fuerza
Q10 ≤ Q50 ≤ Q90 y recorta a [0, capacidad] → valida con Pandera → calcula métricas por pliegue,
por mes y totales → guarda todo en `data/backtest/runs/<run_id>/` y reconstruye la clasificación.
"""

import datetime as dt
import getpass
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from deltalake import DeltaTable

from predico50hz.evaluation import folds as fl
from predico50hz.evaluation.metrics import summarize
from predico50hz.evaluation.store import (
    DEFAULT_ROOT,
    FileRunStore,
    RunRecord,
    RunSink,
    build_leaderboard,
    holdout_count,
    log_holdout,
    now_iso,
    runs_root,
)
from predico50hz.evaluation.validate import validate_predictions
from predico50hz.features.config import DEFAULT_GOLD
from predico50hz.models import MODELS
from predico50hz.models.base import Array, Model, Prediction
from predico50hz.models.baselines import DEFAULT_OPERATOR, load_operator_mw
from predico50hz.models.cqr import calibrate_frame

LOCKFILE = "uv.lock"
KEY = ["forecast_issue_date", "ts_utc"]
PRED_COLUMNS = [
    "forecast_issue_date",
    "target_date",
    "ts_utc",
    "fold",
    "n_steps_day",
    "y_mw",
    "cap_mw",
    "q10_mw",
    "q50_mw",
    "q90_mw",
    "op_q50_mw",
]


def load_gold(path: str | Path = DEFAULT_GOLD) -> tuple[pl.DataFrame, int]:
    """Tabla oro completa y su versión Delta."""
    version = DeltaTable(str(path)).version()
    return pl.read_delta(str(path)), version


def lockfile_hash(path: str | Path = LOCKFILE) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def git_commit() -> str | None:
    """Commit actual de git o `None` (sin git o sin commits todavía)."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def finalize(pred: Prediction, unit: str, cap_mw: Array) -> tuple[dict[str, Array | None], int]:
    """Convierte a MW, fuerza Q10 ≤ Q50 ≤ Q90 y recorta a [0, capacidad].

    El Q50 es el ancla: Q10 se baja hasta Q50 y Q90 se sube hasta Q50 si lo cruzan. Devuelve los
    tres cuantiles y el número de valores que cambió el recorte o el reordenamiento.
    """
    scale = cap_mw if unit == "cf" else np.ones_like(cap_mw)
    q50 = pred.q50 * scale
    q10 = pred.q10 * scale if pred.q10 is not None else None
    q90 = pred.q90 * scale if pred.q90 is not None else None
    changed = 0
    if q10 is not None and q90 is not None:
        lo, hi = np.minimum(q10, q50), np.maximum(q90, q50)
        changed += int(np.count_nonzero((lo != q10) | (hi != q90)))
        q10, q90 = lo, hi
    out: dict[str, Array | None] = {}
    for name, q in (("q10", q10), ("q50", q50), ("q90", q90)):
        if q is None:
            out[name] = None
            continue
        clipped = np.clip(q, 0.0, cap_mw)
        changed += int(np.count_nonzero(clipped != q))
        out[name] = clipped
    return out, changed


def _strip_targets(df: pl.DataFrame) -> pl.DataFrame:
    """Quita las columnas `y_*` (objetivo): el modelo no las ve al predecir."""
    return df.drop([c for c in df.columns if c.startswith("y_")])


def _eval_frame(
    valid: pl.DataFrame, pred: Prediction, model: Model, fold: int, op: pl.DataFrame
) -> tuple[pl.DataFrame, int]:
    cap = valid["y_cap_mw"].to_numpy().astype(np.float64)
    q, changed = finalize(pred, model.unit, cap)
    n = len(cap)

    def series(name: str, q: Array | None) -> pl.Series:
        return pl.Series(name, q if q is not None else [None] * n, dtype=pl.Float64)

    f = valid.select(
        "forecast_issue_date",
        "target_date",
        "ts_utc",
        pl.lit(fold, dtype=pl.Int64).alias("fold"),
        pl.col("n_steps_day").cast(pl.Int64),
        pl.col("y_gen_mw").alias("y_mw"),
        pl.col("y_cap_mw").alias("cap_mw"),
    ).with_columns(
        series("q10_mw", q["q10"]), series("q50_mw", q["q50"]), series("q90_mw", q["q90"])
    )
    f = f.join(op, on="ts_utc", how="left", maintain_order="left").rename({"op_mw": "op_q50_mw"})
    # El operador se trata igual que los modelos: recortado a [0, capacidad].
    f = f.with_columns(
        pl.col("op_q50_mw").clip(lower_bound=0.0, upper_bound=pl.col("cap_mw")).alias("op_q50_mw")
    )
    return f.select(PRED_COLUMNS), changed


def _metrics_block(
    preds: pl.DataFrame, fold_info: list[dict[str, Any]], rank: bool
) -> dict[str, Any]:
    by_fold: list[dict[str, Any]] = []
    for info in fold_info:
        sub = preds.filter(pl.col("fold") == info["fold"])
        by_fold.append({**info, **summarize(sub, rank_vs_operator=rank)})
    months = (
        preds.with_columns(pl.col("target_date").dt.strftime("%Y-%m").alias("month"))
        .get_column("month")
        .unique()
        .sort()
        .to_list()
    )
    by_month = [
        {
            "month": m,
            **summarize(
                preds.filter(pl.col("target_date").dt.strftime("%Y-%m") == m),
                rank_vs_operator=rank,
            ),
        }
        for m in months
    ]
    return {
        "total": summarize(preds, rank_vs_operator=rank),
        "by_fold": by_fold,
        "by_month": by_month,
    }


def run_backtest(  # noqa: PLR0912, PLR0913, PLR0915
    model_name: str,
    *,
    holdout: bool = False,
    gold: str | Path = DEFAULT_GOLD,
    root: str | Path = DEFAULT_ROOT,
    operator: str | Path = DEFAULT_OPERATOR,
    min_train_months: int = fl.DEFAULT_MIN_TRAIN_MONTHS,
    model_params: dict[str, Any] | None = None,
    sinks: list[RunSink] | None = None,
    table: tuple[pl.DataFrame, int] | None = None,
) -> RunRecord:
    """Ejecuta un modelo registrado y guarda la ejecución. `holdout=True` evalúa el reservado.

    `table` permite inyectar (tabla, versión Delta) en pruebas. El modo holdout queda
    registrado en `holdout_log.jsonl` (quién, qué modelo, cuántas veces).
    """
    if model_name not in MODELS:
        raise KeyError(f"modelo desconocido {model_name!r}; disponibles: {sorted(MODELS)}")
    cls = MODELS[model_name]
    if holdout and cls(**(model_params or {})).cqr_config() is not None:
        raise ValueError(
            f"{model_name!r} usa calibración conformal, que necesita el historial de predicciones "
            "walk-forward previo: solo se admite en la validación, no con --holdout"
        )
    t0 = time.monotonic()
    df, version = table if table is not None else load_gold(gold)
    op = load_operator_mw(operator)
    params = dict(model_params or {})
    if cls.is_operator:
        params.setdefault("source", str(operator))

    plan: list[tuple[int, pl.DataFrame, pl.DataFrame, fl.Fold]]
    if holdout:
        train_all, valid_all = fl.split_holdout(df)
        plan = [
            (
                fl.HOLDOUT_FOLD,
                train_all,
                valid_all,
                fl.Fold(fl.HOLDOUT_FOLD, *_span(valid_all)),
            )
        ]
    else:
        plan = []
        for f in fl.dev_folds(df, min_train_months):
            tr, va = fl.split_fold(df, f)
            plan.append((f.index, tr, va, f))

    parts: list[pl.DataFrame] = []
    fold_info: list[dict[str, Any]] = []
    expected: list[pl.DataFrame] = []
    changed_total = 0
    artifacts: dict[str, list[pl.DataFrame]] = {}
    model: Model = cls(**params)
    for index, train, valid, f in plan:
        train_ok = train.drop_nulls("y_cf")
        valid_ok = valid.drop_nulls(["y_gen_mw", "y_cf"])
        if train_ok.height == 0 or valid_ok.height == 0:
            continue
        model = cls(**params)
        model.fit(train_ok)
        pred = model.predict(_strip_targets(valid_ok))
        frame, changed = _eval_frame(valid_ok, pred, model, index, op)
        parts.append(frame)
        expected.append(valid_ok.select(KEY))
        changed_total += changed
        for art, table_art in model.artifacts().items():
            artifacts.setdefault(art, []).append(
                table_art.with_columns(pl.lit(index, dtype=pl.Int64).alias("fold"))
            )
        fold_info.append(
            {
                "fold": index,
                "label": f.label,
                "train_end": f.train_end.isoformat(),
                "valid_start": f.valid_start.isoformat(),
                "valid_end": f.valid_end.isoformat(),
                "n_train_rows": train_ok.height,
                "n_train_days": train_ok["target_date"].n_unique(),
            }
        )
    if not parts:
        raise ValueError("sin pliegues evaluables (¿faltan filas de desarrollo?)")
    preds = pl.concat(parts)
    cqr = model.cqr_config()
    cqr_info: dict[str, Any] | None = None
    if cqr is not None:
        preds, adj = calibrate_frame(preds, cqr)
        artifacts["cqr_adjustment"] = [adj]
        artifacts["cqr_raw_intervals"] = [
            preds.select(*KEY, "q10_raw_mw", "q90_raw_mw", "q10_mw", "q90_mw")
        ]
        preds = preds.select(PRED_COLUMNS)
        cqr_info = {
            **cqr.as_dict(),
            "n_days_calibrated": int(adj["calibrated"].sum()),
            "n_days_uncalibrated": int((~adj["calibrated"]).sum()),
        }
    validate_predictions(preds, pl.concat(expected))
    metrics = _metrics_block(preds, fold_info, rank=not cls.is_operator)

    mode = "holdout" if holdout else "validation"
    created = now_iso()
    stamp = dt.datetime.fromisoformat(created).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{model_name}-{mode}"
    directory = runs_root(root) / run_id
    n = 1
    while directory.exists():
        n += 1
        run_id = f"{stamp}-{model_name}-{mode}-{n}"
        directory = runs_root(root) / run_id

    if artifacts:
        directory.mkdir(parents=True, exist_ok=True)
        for art, frames in artifacts.items():
            pl.concat(frames, how="diagonal").write_parquet(directory / f"{art}.parquet")

    prior = holdout_count(root, model_name) if holdout else None
    prior_total = holdout_count(root)
    run_json: dict[str, Any] = {
        "run_id": run_id,
        "model": model_name,
        "mode": mode,
        "params": model.params(),
        "created_at": created,
        "duration_s": round(time.monotonic() - t0, 2),
        "gold": {"path": str(gold), "delta_version": version, "n_rows_table": df.height},
        "lockfile_sha256": lockfile_hash(),
        "git_commit": git_commit(),
        "min_train_months": min_train_months,
        "folds": fold_info,
        "n_values_clipped_or_reordered": changed_total,
        "holdout_evaluations_before": prior,
        "operator_source": str(operator),
        "cqr": cqr_info,
    }
    record = RunRecord(
        run_id=run_id,
        model=model_name,
        mode=mode,
        params=model.params(),
        run=run_json,
        metrics=metrics,
        predictions=preds,
        directory=directory,
        tags={"mode": mode, "gold_version": str(version)},
    )
    for sink in sinks if sinks is not None else [FileRunStore(root)]:
        sink.save(record)
    if holdout:
        log_holdout(
            root,
            {
                "at": created,
                "run_id": run_id,
                "model": model_name,
                "user": getpass.getuser(),
                "gold_version": version,
                "evaluations_before_for_model": prior,
                "evaluations_before_total": prior_total,
            },
        )
    build_leaderboard(root)
    return record


def _span(valid: pl.DataFrame) -> tuple[dt.date, dt.date]:
    a, b = valid["target_date"].min(), valid["target_date"].max()
    assert isinstance(a, dt.date)
    assert isinstance(b, dt.date)
    return a, b
