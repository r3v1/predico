"""CLI del backtest: `uv run python -m predico50hz.evaluation {run,compare,report}`.

- `run --model <nombre> [--holdout]`: ejecuta un modelo (validación walk-forward o, de forma
  explícita y registrada, el periodo reservado).
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
from predico50hz.evaluation.store import DEFAULT_ROOT, build_leaderboard, holdout_count
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
    rec = run_backtest(
        a.model,
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "compare", "report"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=DEFAULT_ROOT, help="carpeta de salida (data/backtest)")
        if name in {"run", "report"}:
            p.add_argument("--gold", default=DEFAULT_GOLD)
        if name == "run":
            p.add_argument("--model", required=True, choices=sorted(MODELS))
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
    return {"run": _cmd_run, "compare": _cmd_compare, "report": _cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
