# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Capítulo «¿Mejora el pronóstico?»: lee `data/backtest/report/scorecard.json` (F4).

El cuadro de mando lo escribe `uv run python -m predico50hz.evaluation report`. Aquí solo se
recorta a lo que pinta el informe (HTML < 5 MB). Si falta, devuelve `None` y el capítulo muestra
un estado vacío.
"""

from typing import Any

from predico50hz.report.paths import Paths
from predico50hz.report.util import read_json

VALIDATION_KEYS = (
    "run_id",
    "created_at",
    "rmse",
    "mae",
    "bias",
    "winkler",
    "coverage",
    "mean_width",
    "rmse_operator",
    "vs_operator_pct",
    "verdict",
    "vs_best_previous_pct",
    "vs_best_previous_verdict",
    "days_better_than_operator_pct",
    "n_days",
)


def _pick(d: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    return {k: d.get(k) for k in keys} if d else None


def aggregate(paths: Paths) -> dict[str, Any] | None:
    card = read_json(paths.backtest_scorecard)
    if not card or not card.get("models"):
        return None
    models = [
        {
            "model": m["model"],
            "label": m["label"],
            "kind": m["kind"],
            "validation": _pick(m["validation"], VALIDATION_KEYS),
            "holdout": _pick(m.get("holdout"), VALIDATION_KEYS),
            "g1": m["g1"],
            "g2": m["g2"],
            "trend": m["trend"],
            "n_runs": m.get("n_runs", 1),
            "holdout_evaluations": m.get("holdout_evaluations", 0),
            "monthly": [{"month": r["month"], "rmse": r["rmse"]} for r in m.get("monthly", [])],
        }
        for m in card["models"]
    ]
    history = [
        {k: h.get(k) for k in ("run_id", "model", "label", "created_at", "rmse", "rmse_operator")}
        for h in card.get("history", [])
        if h.get("mode") == "validation"
    ]
    return {
        "generated_at": card.get("generated_at"),
        "headline": card.get("headline") or {},
        "rules": card.get("rules") or {},
        "period": card.get("period") or {},
        "holdout_evaluations_total": card.get("holdout_evaluations_total", 0),
        "models": models,
        "history": history,
    }
