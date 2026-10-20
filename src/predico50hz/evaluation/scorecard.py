# ruff: noqa: RUF001, RUF002
"""Cuadro de mando para negocio: `scorecard.json` a partir de las ejecuciones guardadas.

Convenciones (todas explícitas en el JSON, bloque `rules`):

- «Mejora frente al operador» = `(RMSE_operador − RMSE_modelo) / RMSE_operador · 100`, calculado
  sobre exactamente las mismas filas. Positivo = el modelo se equivoca menos.
- Veredicto «mejora / empeora / igual» con umbral `±VERDICT_THRESHOLD_PCT` % de RMSE.
- G1 (PLAN §1): RMSE del Q50 ≤ el del operador **y** cobertura del 80 % entre 76 y 84 %.
- G2: G1 cumplida también en el periodo reservado y RMSE de ese periodo no más de un 10 % peor
  que en validación (control de sobreajuste de F6).
"""

import json
from pathlib import Path
from typing import Any

from predico50hz.evaluation.store import RUNS_DIR, holdout_count, load_runs, now_iso
from predico50hz.models import MODELS

VERDICT_THRESHOLD_PCT = 1.0
COVERAGE_BAND = (0.76, 0.84)
OVERFIT_LIMIT_PCT = 10.0
OPERATOR = "operator"
KEYS = (
    "n_rows",
    "n_days",
    "rmse",
    "mae",
    "bias",
    "winkler",
    "coverage",
    "mean_width",
    "peak_abs_error_mw",
    "peak_bias_mw",
    "peak_shift_h",
    "days_better_than_operator_pct",
    "rmse_operator",
)


def _es(x: float, d: int = 0) -> str:
    return f"{x:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def label(model: str) -> str:
    cls = MODELS.get(model)
    return (cls.label if cls is not None else "") or model


def improvement_pct(reference: float | None, value: float | None) -> float | None:
    """Mejora porcentual de `value` frente a `reference` (positivo = menor error)."""
    if reference is None or value is None or reference == 0:
        return None
    return (reference - value) / reference * 100.0


def verdict(pct: float | None, threshold: float = VERDICT_THRESHOLD_PCT) -> str | None:
    if pct is None:
        return None
    if pct > threshold:
        return "mejora"
    if pct < -threshold:
        return "empeora"
    return "igual"


def _metrics(run: dict[str, Any]) -> dict[str, Any]:
    tot = run["metrics"]["total"]
    return {k: tot.get(k) for k in KEYS}


def _g1(m: dict[str, Any], is_operator: bool) -> dict[str, Any]:
    if is_operator:
        return {
            "status": "no aplica",
            "reasons": ["Es la referencia: no se compara consigo misma."],
        }
    reasons: list[str] = []
    ok = True
    op, rmse = m.get("rmse_operator"), m.get("rmse")
    if op is None or rmse is None:
        ok = False
        reasons.append("No hay pronóstico del operador con el que comparar.")
    elif rmse <= op:
        reasons.append(f"Error de {_es(rmse)} MW, menor o igual que los {_es(op)} MW del operador.")
    else:
        ok = False
        reasons.append(
            f"Error de {_es(rmse)} MW frente a {_es(op)} MW del operador: "
            f"un {_es((rmse / op - 1) * 100, 1)} % peor."
        )
    cov = m.get("coverage")
    lo, hi = COVERAGE_BAND
    if cov is None:
        ok = False
        reasons.append("El modelo no da rango (Q10–Q90): no se puede medir la cobertura del 80 %.")
    elif lo <= cov <= hi:
        reasons.append(
            f"El rango del 80 % acierta el {_es(cov * 100, 1)} % de las veces (banda 76–84 %)."
        )
    else:
        ok = False
        reasons.append(
            f"El rango del 80 % acierta el {_es(cov * 100, 1)} % de las veces, "
            f"fuera de la banda 76–84 %."
        )
    return {"status": "cumple" if ok else "no cumple", "reasons": reasons}


def _g2(
    val: dict[str, Any], hold: dict[str, Any] | None, hold_g1: dict[str, Any] | None
) -> dict[str, Any]:
    if hold is None or hold_g1 is None:
        return {"status": "sin evaluar", "reasons": ["El periodo reservado no se ha evaluado."]}
    reasons = list(hold_g1["reasons"])
    ok = hold_g1["status"] == "cumple"
    if val.get("rmse") and hold.get("rmse") is not None:
        worse = (hold["rmse"] / val["rmse"] - 1) * 100
        if worse > OVERFIT_LIMIT_PCT:
            ok = False
            reasons.append(
                f"El error en el periodo reservado es un {_es(worse, 1)} % mayor que en validación "
                f"(límite {OVERFIT_LIMIT_PCT:.0f} %): posible sobreajuste."
            )
        else:
            reasons.append(
                f"El error en el periodo reservado difiere un {_es(worse, 1)} % del de validación."
            )
    return {"status": "cumple" if ok else "no cumple", "reasons": reasons}


def _monthly(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "month": m["month"],
            "rmse": m.get("rmse"),
            "rmse_operator": m.get("rmse_operator"),
            "coverage": m.get("coverage"),
            "winkler": m.get("winkler"),
        }
        for m in run["metrics"]["by_month"]
    ]


def _trend(model_runs: list[dict[str, Any]]) -> dict[str, Any]:
    last = model_runs[-1]
    if len(model_runs) < 2:  # noqa: PLR2004
        return {"previous_run_id": None, "change_pct": None, "direction": "primera ejecución"}
    prev = model_runs[-2]
    pct = improvement_pct(prev["metrics"]["total"]["rmse"], last["metrics"]["total"]["rmse"])
    return {
        "previous_run_id": prev["run_id"],
        "change_pct": pct,
        "direction": verdict(pct),
    }


def _entry(model: str, runs: list[dict[str, Any]], all_val: list[dict[str, Any]]) -> dict[str, Any]:
    cls = MODELS.get(model)
    is_op = bool(cls and cls.is_operator)
    vruns = [r for r in runs if r["mode"] == "validation"]
    last = vruns[-1]
    m = _metrics(last)
    vs_op = improvement_pct(m["rmse_operator"], m["rmse"]) if not is_op else None
    earlier = [
        r
        for r in all_val
        if (r["created_at"], r["run_id"]) < (last["created_at"], last["run_id"])
        and r["model"] != OPERATOR
    ]
    best_prev = min(earlier, key=lambda r: r["metrics"]["total"]["rmse"]) if earlier else None
    vs_prev = (
        improvement_pct(best_prev["metrics"]["total"]["rmse"], m["rmse"]) if best_prev else None
    )
    hruns = [r for r in runs if r["mode"] == "holdout"]
    hold = _metrics(hruns[-1]) if hruns else None
    g1 = _g1(m, is_op)
    hold_g1 = _g1(hold, is_op) if hold else None
    return {
        "model": model,
        "label": label(model),
        "kind": "operador" if is_op else "referencia",
        "validation": {
            "run_id": last["run_id"],
            "created_at": last["created_at"],
            **m,
            "vs_operator_pct": vs_op,
            "verdict": verdict(vs_op) if not is_op else None,
            "vs_best_previous_pct": vs_prev,
            "vs_best_previous_run": best_prev["run_id"] if best_prev else None,
            "vs_best_previous_verdict": verdict(vs_prev) if best_prev else "primera ejecución",
        },
        "holdout": (
            {
                "run_id": hruns[-1]["run_id"],
                "created_at": hruns[-1]["created_at"],
                **hold,
                "vs_operator_pct": improvement_pct(hold["rmse_operator"], hold["rmse"])
                if not is_op
                else None,
                "verdict": verdict(improvement_pct(hold["rmse_operator"], hold["rmse"]))
                if not is_op
                else None,
            }
            if hold
            else None
        ),
        "g1": g1,
        "g2": (
            {"status": "no aplica", "reasons": ["Es la referencia."]}
            if is_op
            else _g2(m, hold, hold_g1)
        ),
        "trend": _trend(vruns),
        "n_runs": len(vruns),
        "monthly": _monthly(last),
    }


def _headline(models: list[dict[str, Any]]) -> dict[str, Any]:
    cands = [m for m in models if m["kind"] != "operador"]
    if not cands:
        return {"verdict": None, "best_model": None, "text": "Aún no hay ningún modelo evaluado."}
    best = min(cands, key=lambda m: m["validation"]["rmse"])
    v = best["validation"]
    pct = v["vs_operator_pct"]
    name = best["label"].lower()
    if pct is None:
        text = "No se puede comparar con el operador: faltan sus datos."
    elif v["verdict"] == "mejora":
        text = (
            f"Sí: el mejor modelo ({name}) se equivoca un {_es(abs(pct), 1)} % menos que el "
            f"operador ({_es(v['rmse'])} MW frente a {_es(v['rmse_operator'])} MW)."
        )
    elif v["verdict"] == "empeora":
        text = (
            f"Todavía no: el mejor modelo ({name}) se equivoca un {_es(abs(pct), 1)} % más que "
            f"el operador ({_es(v['rmse'])} MW frente a {_es(v['rmse_operator'])} MW)."
        )
    else:
        text = (
            f"Empate técnico: el mejor modelo ({name}) y el operador se equivocan lo mismo "
            f"({_es(v['rmse'])} MW frente a {_es(v['rmse_operator'])} MW, diferencia dentro "
            f"del ±{_es(VERDICT_THRESHOLD_PCT)} %)."
        )
    return {
        "verdict": v["verdict"],
        "best_model": best["model"],
        "best_label": best["label"],
        "improvement_vs_operator_pct": pct,
        "g1": best["g1"]["status"],
        "text": text,
    }


def build_scorecard(root: str | Path, gold: str | None = None) -> dict[str, Any]:
    """Construye el cuadro de mando desde `<root>/runs/*`. Sin ejecuciones de validación: error."""
    runs = load_runs(root)
    all_val = [r for r in runs if r["mode"] == "validation"]
    if not all_val:
        raise ValueError(f"no hay ejecuciones de validación en {Path(root) / RUNS_DIR}")
    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        by_model.setdefault(r["model"], []).append(r)
    models = [
        _entry(m, rs, all_val)
        for m, rs in by_model.items()
        if any(r["mode"] == "validation" for r in rs)
    ]
    models.sort(key=lambda m: m["validation"]["rmse"])
    for m in models:
        m["holdout_evaluations"] = holdout_count(root, m["model"])
    gold_versions = sorted({r["gold"]["delta_version"] for r in runs})
    first = all_val[-1]
    return {
        "generated_at": now_iso(),
        "rules": {
            "verdict_threshold_pct": VERDICT_THRESHOLD_PCT,
            "coverage_band": list(COVERAGE_BAND),
            "overfit_limit_pct": OVERFIT_LIMIT_PCT,
            "improvement_definition": "(RMSE operador - RMSE modelo) / RMSE operador * 100",
        },
        "period": {
            "validation_start": first["folds"][0]["valid_start"],
            "validation_end": first["folds"][-1]["valid_end"],
            "n_folds": len(first["folds"]),
        },
        "gold_versions": gold_versions,
        "holdout_evaluations_total": holdout_count(root),
        "headline": _headline(models),
        "models": models,
        "history": [
            {
                "run_id": r["run_id"],
                "model": r["model"],
                "label": label(r["model"]),
                "mode": r["mode"],
                "created_at": r["created_at"],
                "rmse": r["metrics"]["total"].get("rmse"),
                "rmse_operator": r["metrics"]["total"].get("rmse_operator"),
                "winkler": r["metrics"]["total"].get("winkler"),
                "coverage": r["metrics"]["total"].get("coverage"),
            }
            for r in runs
        ],
    }


def write_scorecard(card: dict[str, Any], out: str | Path) -> Path:
    path = Path(out) / "scorecard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    return path
