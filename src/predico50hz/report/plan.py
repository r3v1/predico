# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# ruff: noqa: PLR2004, RUF001
"""Estado del proyecto leído de las marcas de `PLAN.md` (✅ hecho, 🔄 en curso, 🟡 parcial)."""

import datetime as dt
import re
from pathlib import Path
from typing import Any

from predico50hz.report.paths import LEAGUE_START

MARKS = {"✅": "done", "🔄": "progress", "🟡": "partial"}
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    )
}
FRIENDLY_BLOCKS = {
    "F1": "Datos meteorológicos",
    "F2": "Producción real, precios y parques",
    "F3": "Variables del modelo",
    "F4": "Banco de pruebas",
    "F5": "Referencias de comparación",
    "F6": "Modelo principal",
    "F7": "Modelos de IA preentrenados",
    "F8": "Calidad e ingeniería",
    "F9": "Cierre de la fase",
}
MARK_RE = re.compile("[" + "".join(MARKS) + "]")
TASK_RE = re.compile(r"^- (F\d\.\d+)\b\s*(" + "|".join(MARKS) + r")?")
BULLET_RE = re.compile(r"^- (" + "|".join(MARKS) + r")\s*(.*)$")


def clean(text: str, *, cut: bool = True) -> str:
    """Quita el marcado Markdown y, si `cut`, las referencias a ficheros (`→ ...`)."""
    text = text.split(" → ", maxsplit=1)[0] if cut else text
    text = re.sub(r"[*`]", "", text)
    return MARK_RE.sub("", text).strip()


def _mark(text: str) -> str:
    m = MARK_RE.search(text)
    return MARKS[m.group()] if m else "pending"


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for ln in lines:
        if ln.startswith("|") and not re.match(r"^\|[\s|:-]+\|$", ln):
            rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return rows


def _section(lines: list[str], pattern: str) -> list[str]:
    """Líneas de la sección cuyo encabezado casa con `pattern`, hasta el siguiente encabezado."""
    for i, ln in enumerate(lines):
        if re.match(pattern, ln):
            level = len(ln) - len(ln.lstrip("#"))
            out: list[str] = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith("#") and len(nxt) - len(nxt.lstrip("#")) <= level:
                    break
                out.append(nxt)
            return out
    return []


def _parse_date(cell: str, year: int) -> tuple[dt.date, dt.date] | None:
    m = re.search(r"(\d{1,2})(?:\s*[–-]\s*(\d{1,2}))?\s+([a-z]{3})", clean(cell).lower())
    if not m or m.group(3) not in MONTHS:
        return None
    month = MONTHS[m.group(3)]
    start = dt.date(year, month, int(m.group(1)))
    return start, dt.date(year, month, int(m.group(2) or m.group(1)))


def milestones(lines: list[str], year: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _table_rows(_section(lines, r"^## 1\.")):
        if len(row) < 3:
            continue
        dates = _parse_date(row[0], year)
        if dates is None:
            continue
        gate = re.search(r"\b(G\d)\b", row[2])
        out.append(
            {
                "date": dates[0].isoformat(),
                "end": dates[1].isoformat(),
                "key": clean(row[0]),
                "label": clean(row[1]),
                "gate": gate.group(1) if gate else None,
                "gate_text": clean(row[2]).split(":", 1)[-1].strip() if row[2] else "",
                "status": _mark(row[1]),
            }
        )
    return out


def rules(lines: list[str]) -> list[str]:
    """Viñetas bajo «Reglas de corte» del calendario."""
    sec = _section(lines, r"^## 1\.")
    out: list[str] = []
    on = False
    for ln in sec:
        if "Reglas de corte" in ln:
            on = True
        elif on and ln.startswith("- "):
            out.append(clean(ln[2:], cut=False))
        elif on and ln.startswith("**") and out:
            break
    return out


def stages(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _table_rows(_section(lines, r"^### 2\.5")):
        m = re.match(r"^(?:(" + "|".join(MARKS) + r")\s*)?\*\*(\d)\*\*", row[0])
        if m:
            out.append(
                {
                    "id": m.group(2),
                    "status": MARKS.get(m.group(1) or "", "pending"),
                    "what": clean(row[1], cut=False) if len(row) > 1 else "",
                }
            )
    return out


def blocks(lines: list[str]) -> list[dict[str, Any]]:
    """Un bloque por encabezado `### F<n> — ...`, con el recuento de sus tareas por estado."""
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        h = re.match(r"^### (F\d) — (.+)$", ln)
        if not h:
            continue
        counts = dict.fromkeys(("done", "progress", "partial", "pending"), 0)
        bullets = dict(counts)
        for nxt in lines[i + 1 :]:
            if nxt.startswith(("## ", "### ")):
                break
            t = TASK_RE.match(nxt)
            if t:
                counts[MARKS.get(t.group(2) or "", "pending")] += 1
            b = BULLET_RE.match(nxt)
            if b and not t:
                bullets[MARKS[b.group(1)]] += 1
        total = sum(counts.values())
        marked = sum(v for k, v in (counts if total else bullets).items() if k != "pending")
        use = counts if total else bullets
        if total and counts["done"] == total:
            status = "done"
        elif marked or any(use[k] for k in ("done", "progress", "partial")):
            status = "progress"
        else:
            status = "pending"
        out.append(
            {
                "id": h.group(1),
                "title": FRIENDLY_BLOCKS.get(h.group(1), clean(h.group(2))),
                "status": status,
                "total": total,
                **{k: counts[k] for k in ("done", "progress", "partial")},
                "pending": counts["pending"],
            }
        )
    return out


def parse_plan(path: Path, today: dt.date) -> dict[str, Any] | None:
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return None
    ver = next((ln for ln in lines if ln.startswith("**Versión")), "")
    y = re.search(r"\b(20\d\d)\b", ver)
    year = int(y.group(1)) if y else today.year
    ms = milestones(lines, year)
    league = dt.date(*LEAGUE_START)
    return {
        "version": clean(ver).split("·")[0].replace("Versión", "").strip(),
        "milestones": ms,
        "gates": [m for m in ms if m["gate"]],
        "rules": rules(lines),
        "stages": stages(lines),
        "blocks": blocks(lines),
        "league_start": league.isoformat(),
        "days_to_league": (league - today).days,
    }
