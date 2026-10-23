"""CLI del backtest: `uv run python -m predico50hz.evaluation {run,compare,report}`.

- `run --model <nombre> [--holdout]`: ejecuta un modelo (validación walk-forward o, de forma
  explícita y registrada, el periodo reservado).
- `mlflow-sync`: registra en MLflow (`data/mlflow`) las ejecuciones ya guardadas en disco.
- `tune --quantile q50 --trials N`: estudio de Optuna de un cuantil (#32, `models/tuning.py`).
- `tune-export`: escribe el mejor intento de cada estudio en `config/lgbm_tuned.yaml`.
- `compare`: tabla de clasificación de las últimas ejecuciones de cada modelo.
- `report`: `scorecard.json` y gráficos para negocio en `data/backtest/report/`.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

from predico50hz.evaluation import folds as fl
from predico50hz.evaluation.backtest import run_backtest
from predico50hz.evaluation.mlflow_sink import DEFAULT_TRACKING_URI
from predico50hz.evaluation.store import (
    DEFAULT_ROOT,
    FileRunStore,
    RunSink,
    build_leaderboard,
    holdout_count,
)
from predico50hz.features.config import DEFAULT_GOLD
from predico50hz.models import MODELS
from predico50hz.models.baselines import DEFAULT_OPERATOR


def _param(s: str) -> tuple[str, Any]:
    k, _, v = s.partition("=")
    try:
        return k, json.loads(v)
    except ValueError:
        return k, v


def _fmt(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x:,.{d}f}"


def _cmd_run(a: argparse.Namespace) -> int:
    if a.holdout and holdout_count(a.root, a.model):
        print(
            f"AVISO: el periodo reservado ya se evaluó {holdout_count(a.root, a.model)} vez/veces "
            f"con {a.model!r} ({holdout_count(a.root)} en total). Cada mirada lo desgasta.",
            file=sys.stderr,
        )
    sinks: list[RunSink] | None = None
    if a.mlflow:
        from predico50hz.evaluation.mlflow_sink import MlflowRunSink  # noqa: PLC0415

        sinks = [FileRunStore(a.root), MlflowRunSink(a.mlflow_uri)]
    rec = run_backtest(
        a.model,
        sinks=sinks,
        holdout=a.holdout,
        gold=a.gold,
        root=a.root,
        operator=a.operator,
        min_train_months=a.min_train_months,
        model_params=dict(a.param),
    )
    t = rec.metrics["total"]
    print(f"run {rec.run_id}  ({rec.directory})")
    print(
        f"  filas {t['n_rows']:,}  días {t['n_days']}  RMSE {_fmt(t['rmse'])} MW  "
        f"operador {_fmt(t['rmse_operator'])} MW  Winkler {_fmt(t['winkler'])}  "
        f"cobertura {_fmt(t['coverage'] * 100 if t['coverage'] is not None else None)} %"
    )
    return 0


def _cmd_compare(a: argparse.Namespace) -> int:
    lb = build_leaderboard(a.root)
    if lb.height == 0:
        print("sin ejecuciones")
        return 0
    latest = lb.filter(pl.col("is_latest")).sort("mode", "rmse")
    print(
        f"{'modo':<11}{'modelo':<14}{'RMSE':>8}{'operador':>10}{'Winkler':>9}{'cobert.%':>9}"
        f"{'ancho':>9}{'días>op %':>10}  run"
    )
    for r in latest.iter_rows(named=True):
        cov = None if r["coverage"] is None else r["coverage"] * 100
        print(
            f"{r['mode']:<11}{r['model']:<14}{_fmt(r['rmse']):>8}{_fmt(r['rmse_operator']):>10}"
            f"{_fmt(r['winkler']):>9}{_fmt(cov):>9}{_fmt(r['mean_width']):>9}"
            f"{_fmt(r['days_better_than_operator_pct'], 0):>10}  {r['run_id']}"
        )
    return 0


def _cmd_report(a: argparse.Namespace) -> int:
    from predico50hz.evaluation.report import build_report  # noqa: PLC0415 (matplotlib es lento)

    out = build_report(a.root, a.out, gold=a.gold, png=not a.no_png)
    print(f"informe en {out}")
    return 0


def _cmd_mlflow_sync(a: argparse.Namespace) -> int:
    from predico50hz.evaluation.mlflow_sink import MlflowRunSink, sync  # noqa: PLC0415

    new, old = sync(a.root, MlflowRunSink(a.mlflow_uri))
    print(f"MLflow ({a.mlflow_uri}): {new} ejecuciones registradas, {old} ya existían")
    return 0


def _cmd_tune(a: argparse.Namespace) -> int:
    from predico50hz.models import tuning  # noqa: PLC0415 (Optuna es lento de importar)

    study = tuning.run_study(
        a.quantile,
        a.trials,
        workers=a.workers,
        db=a.db,
        prefix=a.prefix,
        gold=a.gold,
        folds=a.folds,
        min_train_months=a.min_train_months,
        num_threads=a.threads,
        space=a.space,
        mlflow_uri=a.mlflow_uri if a.mlflow else None,
    )
    s = tuning.summarize_study(study)
    print(f"estudio {s['study']}: {s['counts']}")
    print(
        f"  intento 0 (v0.1): {_fmt(s['baseline_value'], 2)} MW   mejor (nº {s['best_number']}): "
        f"{_fmt(s['best_value'], 2)} MW"
    )
    print(f"  mejores parámetros: {json.dumps(s['best_params'], ensure_ascii=False)}")
    print(f"  importancia: {json.dumps(s['importance'], ensure_ascii=False)}")
    return 0


def _cmd_tune_export(a: argparse.Namespace) -> int:
    from predico50hz.models import tuning  # noqa: PLC0415

    best = tuning.export_params(a.db, a.out, prefix=a.prefix, note=a.note)
    print(f"{a.out}: {json.dumps(best, ensure_ascii=False)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "compare", "report", "mlflow-sync", "tune", "tune-export"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=DEFAULT_ROOT, help="carpeta de salida (data/backtest)")
        if name in {"run", "mlflow-sync", "tune"}:
            p.add_argument("--mlflow-uri", default=DEFAULT_TRACKING_URI, help="tracking URI")
        if name in {"run", "report", "tune"}:
            p.add_argument("--gold", default=DEFAULT_GOLD)
        if name in {"tune", "tune-export"}:
            p.add_argument("--db", default="data/optuna/predico.db", help="SQLite de Optuna")
            p.add_argument("--prefix", default="lgbm", help="prefijo del nombre del estudio")
        if name == "tune":
            p.add_argument("--quantile", required=True, choices=["q50", "q10", "q90"])
            p.add_argument("--trials", type=int, default=150, help="intentos nuevos (150 a 300)")
            p.add_argument("--workers", type=int, default=24, help="procesos de Optuna")
            p.add_argument("--threads", type=int, default=1, help="hilos de LightGBM por proceso")
            p.add_argument(
                "--folds", default="even", help="all | even | odd | índices separados por comas"
            )
            p.add_argument(
                "--space", default="adr", choices=["adr", "wide"], help="espacio de búsqueda"
            )
            p.add_argument("--mlflow", action="store_true", help="registrar el estudio en MLflow")
            p.add_argument("--min-train-months", type=int, default=fl.DEFAULT_MIN_TRAIN_MONTHS)
        if name == "tune-export":
            p.add_argument("--out", default="config/lgbm_tuned.yaml")
            p.add_argument("--note", default="")
        if name == "run":
            p.add_argument("--model", required=True, choices=sorted(MODELS))
            p.add_argument("--mlflow", action="store_true", help="registrar también en MLflow")
            p.add_argument("--holdout", action="store_true", help="evaluar el periodo reservado")
            p.add_argument("--operator", default=DEFAULT_OPERATOR)
            p.add_argument("--min-train-months", type=int, default=fl.DEFAULT_MIN_TRAIN_MONTHS)
            p.add_argument("--param", action="append", default=[], type=_param, metavar="K=V")
        if name == "report":
            p.add_argument("--out", default=None, help="por defecto <root>/report")
            p.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "report" and a.out is None:
        a.out = str(Path(a.root) / "report")
    return {
        "run": _cmd_run,
        "compare": _cmd_compare,
        "report": _cmd_report,
        "mlflow-sync": _cmd_mlflow_sync,
        "tune": _cmd_tune,
        "tune-export": _cmd_tune_export,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
