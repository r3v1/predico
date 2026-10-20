# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Ensambla los datos de todos los capítulos y los incrusta en la plantilla HTML."""

import base64
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from predico50hz.ingest.dynamical.config import LEAD_HOURS, N_MEMBERS, VARIABLES
from predico50hz.report import (
    backtest,
    content,
    forecast,
    plan,
    production,
    quality,
    sources,
    zone,
)
from predico50hz.report.paths import LEAGUE_START, PACKAGE, VENDOR, Paths
from predico50hz.report.util import read_json

FONTS = [  # (familia, peso, estilo, fichero)
    ("Inter", 400, "normal", "inter-latin-400-normal"),
    ("Inter", 500, "normal", "inter-latin-500-normal"),
    ("Inter", 600, "normal", "inter-latin-600-normal"),
    ("Fraunces", 400, "normal", "fraunces-latin-400-normal"),
    ("Fraunces", 600, "normal", "fraunces-latin-600-normal"),
    ("Fraunces", 400, "italic", "fraunces-latin-400-italic"),
]


def fmt_es(x: float, decimals: int = 0) -> str:
    """Número con formato español: punto de miles y coma decimal."""
    s = f"{x:,.{decimals}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return s


class _Facts(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(text: str, facts: dict[str, str]) -> str:
    return text.format_map(_Facts(facts))


def _facts(data: dict[str, Any]) -> dict[str, str]:
    z = data.get("zone") or {}
    jump = ((data.get("production") or {}).get("daily") or {}).get("sea_jump") or {}
    q = data.get("quality") or {}
    f: dict[str, str] = {
        "n_members": str(N_MEMBERS),
        "n_groups": str(z.get("n_groups", "")),
        "n_turbines": fmt_es(z["n_turbines"]) if z else "",
        "coverage_pct": fmt_es(q["coverage"] * 100) if q.get("coverage") is not None else "",
        "days_to_league": str((data.get("plan") or {}).get("days_to_league", "")),
    }
    if jump:
        f |= {
            "sea_jump_pct": fmt_es(jump["pct"]),
            "sea_now_mw": fmt_es(jump["now_mw"]),
            "sea_before_mw": fmt_es(jump["before_mw"]),
        }
    return f


def _plan(paths: Paths, today: dt.date, labels: dict[str, Any]) -> dict[str, Any] | None:
    p = plan.parse_plan(paths.plan, today)
    if p is None:
        return None
    ms = labels.get("milestones") or {}
    for m in p["milestones"]:
        over = ms.get(m["key"])
        if isinstance(over, dict):
            m["label"] = over.get("label", m["label"])
            m["gate_text"] = over.get("detail", m["gate_text"])
    st = labels.get("stages") or {}
    for s in p["stages"]:
        s["what"] = st.get(s["id"], s["what"])
    return p


def _kpis(data: dict[str, Any]) -> list[dict[str, Any]]:
    z, q = data.get("zone"), data.get("quality")
    prod = (data.get("production") or {}).get("daily")
    nwp = next((r for r in (q or {}).get("rows", []) if r["key"] == "nwp"), None)
    return [
        {
            "key": "turbines",
            "label": "aerogeneradores activos",
            "value": z["n_turbines"] if z else None,
            "fmt": "int",
        },
        {
            "key": "gw",
            "label": "de potencia instalada",
            "value": z["mw_total"] / 1000 if z else None,
            "fmt": "dec1",
            "unit": "GW",
        },
        {
            "key": "days",
            "label": "días de previsión guardados",
            "value": nwp["n_ok"] if nwp else None,
            "fmt": "int",
        },
        {
            "key": "years",
            "label": "años de producción real",
            "value": prod["n_days"] / 365.25 if prod else None,
            "fmt": "dec1",
        },
        {
            "key": "coverage",
            "label": "de cobertura de calidad",
            "value": q["coverage"] * 100 if q and q.get("coverage") is not None else None,
            "fmt": "int",
            "unit": "%",
        },
        {
            "key": "league",
            "label": "días para la liga",
            "value": data["plan"]["days_to_league"] if data.get("plan") else None,
            "fmt": "int",
        },
    ]


def _variables(doc: dict[str, Any], fc: dict[str, Any]) -> dict[str, Any]:
    """Una tarjeta por variable de la ingesta (y por cada velocidad derivada), con su texto."""
    text = {v["id"]: v for v in doc.get("variables", []) if isinstance(v, dict)}
    order = [*fc["variables"], *fc["derived"]]
    cards = [
        {
            "name": i,
            "unit": "",
            "group": "context",
            "icon": "isobar",
            "why": "",
            **text.get(i, {}),
            "id": i,
        }
        for i in order
    ]
    rank = {v["id"]: n for n, v in enumerate(doc.get("variables", []))}
    cards.sort(key=lambda c: rank.get(c["id"], len(rank)))
    return {"groups": doc.get("groups", {}), "cards": cards}


def build_data(paths: Paths, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    today = now.date()
    cont = content.load(paths)
    data: dict[str, Any] = {
        "meta": {
            "generated_at": now.isoformat(timespec="seconds"),
            "today": today.isoformat(),
            "league_start": dt.date(*LEAGUE_START).isoformat(),
            "n_members": N_MEMBERS,
            "n_variables": len(VARIABLES),
            "lead_hours": [LEAD_HOURS[0], LEAD_HOURS[-1]],
            "versions": sources.versions(paths),
        },
        "plan": _plan(paths, today, cont["labels"]),
        "changelog": cont["changelog"],
        "zone": zone.aggregate(paths),
        "quality": quality.aggregate(paths, today),
        "clock": sources.clock(paths, today),
        "volumes": sources.volumes(paths),
        "model": cont["model"],
    }
    groups = (data["zone"] or {}).get("groups", [])
    data["production"] = production.aggregate(paths, groups)
    data["forecast"] = forecast.aggregate(paths, groups)
    data["variables"] = _variables(cont["variables"], data["forecast"])
    data["backtest"] = backtest.aggregate(paths)
    data["clock_notes"] = cont["clock"]
    data["glossary"] = cont["glossary"]
    facts = _facts(data)
    data["insights"] = [
        {k: fill(v, facts) if isinstance(v, str) else v for k, v in i.items()}
        for i in cont["insights"]
    ]
    data["sources"] = {
        "cards": cont["sources"],
        "places": cont["places"],
        "routes": cont["routes"],
    }
    j = cont["journey"]
    data["journey"] = {
        "columns": j.get("columns", []),
        "next": j.get("next", {}),
        "lanes": [
            {**lane, "steps": {k: fill(v, facts) for k, v in lane.get("steps", {}).items()}}
            for lane in j.get("lanes", [])
        ],
    }
    data["kpis"] = _kpis(data)
    nwp_meta = read_json(paths.stage0) or {}
    data["meta"]["nwp_first_init"] = (nwp_meta.get("dataset") or {}).get("init_time_first")
    return data


def _font_css() -> str:
    out: list[str] = []
    for family, weight, style, name in FONTS:
        b64 = base64.b64encode((VENDOR / "fonts" / f"{name}.woff2").read_bytes()).decode("ascii")
        out.append(
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"font-display:swap;src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
        )
    return "\n".join(out)


def _iso(obj: Any) -> str:
    if isinstance(obj, dt.date):  # YAML interpreta las fechas AAAA-MM-DD como date
        return obj.isoformat()
    raise TypeError(f"no serializable: {type(obj).__name__}")


def _json(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_iso)
    return text.replace("</", "<\\/")


def render(data: dict[str, Any]) -> str:
    geo = {
        "zone": json.loads((VENDOR / "geo" / "zone.geojson").read_text("utf-8")),
        "world": json.loads((VENDOR / "geo" / "world.geojson").read_text("utf-8")),
    }
    parts = {
        "FONTS": _font_css(),
        "CSS": (PACKAGE / "report.css").read_text("utf-8"),
        "ECHARTS": (VENDOR / "echarts.min.js").read_text("utf-8").replace("</script", "<\\/script"),
        "DATA": _json(data),
        "GEO": _json(geo),
        "JS": (PACKAGE / "report.js").read_text("utf-8"),
    }
    template = (PACKAGE / "template.html").read_text("utf-8")
    return re.sub(r"\{\{([A-Z]+)\}\}", lambda m: parts[m.group(1)], template)


def build(paths: Paths, out: Path, now: dt.datetime | None = None, dump_json: bool = True) -> Path:
    data = build_data(paths, now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), "utf-8")
    if dump_json:
        out.with_name("data.json").write_text(_json(data), "utf-8")
    return out
