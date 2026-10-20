# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# ruff: noqa: RUF001
"""Estado del proyecto leído de GitHub (milestones e issues), con caché local."""

import datetime as dt
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from predico50hz.report.paths import LEAGUE_START
from predico50hz.report.util import read_json

REPO = "r3v1/predico"
TIMEOUT_S = 20
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
BACKLOG_RE = re.compile(r"<!--\s*backlog:(\S+?)\s*-->")
Fetch = Callable[[str], list[dict[str, Any]]]


def gh_api(endpoint: str) -> list[dict[str, Any]]:
    """GET paginado con `gh api`; lanza si no hay `gh`, red o autenticación."""
    out = subprocess.run(
        ["gh", "api", "--paginate", endpoint],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=True,
    ).stdout
    dec, i, items = json.JSONDecoder(), 0, []
    while i < len(out):  # `--paginate` concatena una lista JSON por página
        if out[i].isspace():
            i += 1
            continue
        page, i = dec.raw_decode(out, i)
        items += page
    return items


def reduce(milestones: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Respuesta de GitHub reducida a lo que usa el informe (es lo que se guarda en la caché)."""
    out_issues = []
    for i in issues:
        if "pull_request" in i:
            continue
        mark = BACKLOG_RE.search(i.get("body") or "")
        sub = i.get("sub_issues_summary") or {}
        out_issues.append(
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "labels": [lb["name"] for lb in i.get("labels", [])],
                "milestone": (i.get("milestone") or {}).get("number"),
                "backlog": mark.group(1) if mark else None,
                "sub_total": sub.get("total", 0),
                "sub_done": sub.get("completed", 0),
            }
        )
    return {
        "milestones": [
            {
                "number": m["number"],
                "title": m["title"],
                "due_on": m.get("due_on"),
                "open": m["open_issues"],
                "closed": m["closed_issues"],
                "state": m["state"],
            }
            for m in milestones
        ],
        "issues": out_issues,
    }


def load(
    cache: Path, now: dt.datetime, *, offline: bool = False, fetch: Fetch = gh_api
) -> dict[str, Any] | None:
    """GitHub → caché → `None`. Añade `source` («github» o «cache») y `fetched` (ISO)."""
    if not offline:
        try:
            raw = reduce(
                fetch(f"repos/{REPO}/milestones?state=all&per_page=100"),
                fetch(f"repos/{REPO}/issues?state=all&per_page=100"),
            )
            raw["fetched"] = now.isoformat(timespec="seconds")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
            return {**raw, "source": "github"}
        except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
            pass
    raw = read_json(cache)
    if isinstance(raw, dict) and "milestones" in raw and "issues" in raw:
        return {**raw, "source": "cache"}
    return None


def _status(closed: int, total: int, *, state: str = "open") -> str:
    if state == "closed" or (total and closed == total):
        return "done"
    return "progress" if closed else "pending"


def _short(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def _milestones(raw: dict[str, Any], labels: dict[str, Any]) -> list[dict[str, Any]]:
    over = labels.get("milestones") or {}
    out: list[dict[str, Any]] = []
    for p in labels.get("past_milestones") or []:
        d = dt.date.fromisoformat(str(p["date"]))
        out.append(
            {
                "date": d.isoformat(),
                "end": d.isoformat(),
                "key": _short(d),
                "label": p["label"],
                "gate": None,
                "gate_text": p.get("detail", ""),
                "status": "done",
            }
        )
    issues = raw["issues"]
    for m in sorted(raw["milestones"], key=lambda m: int(re.match(r"\d+", m["title"]).group())):
        n = int(re.match(r"\d+", m["title"]).group())
        o = over.get(n) or over.get(str(n)) or {}
        end = (
            dt.datetime.fromisoformat(m["due_on"].replace("Z", "+00:00")).date()
            if m.get("due_on")
            else dt.date(*LEAGUE_START)
        )
        start = dt.date.fromisoformat(str(o["start"])) if o.get("start") else end
        total = m["open"] + m["closed"]
        gate = next(
            (
                g.group(1)
                for i in issues
                if i["milestone"] == n and "tipo:puerta" in i["labels"]
                for g in [re.match(r"(G\d)\b", i["title"])]
                if g
            ),
            None,
        )
        out.append(
            {
                "date": start.isoformat(),
                "end": end.isoformat(),
                "key": _short(start) if start == end else f"{start.day}–{_short(end)}",
                "label": o.get("label", re.sub(r"^\d+\s*·\s*", "", m["title"])),
                "gate": gate,
                "gate_text": o.get("detail", ""),
                "status": _status(m["closed"], total, state=m["state"]),
                "closed": m["closed"],
                "total": total,
            }
        )
    return out


def _epics(raw: dict[str, Any], labels: dict[str, Any]) -> list[dict[str, Any]]:
    titles = labels.get("blocks") or {}
    out: list[dict[str, Any]] = []
    for i in raw["issues"]:
        if "tipo:epic" not in i["labels"] or not i["sub_total"]:
            continue
        done, total = i["sub_done"], i["sub_total"]
        tag = re.match(r"F\d(?:\.\d)?", i["title"])
        out.append(
            {
                "id": tag.group() if tag else "",
                "title": titles.get(i["backlog"], re.sub(r"^F\d(?:\.\d)?\s*·\s*", "", i["title"])),
                "status": _status(done, total, state=i["state"]),
                "total": total,
                "done": done,
                "progress": 0,
                "partial": 0,
                "pending": total - done,
            }
        )
    return out


def _stages(raw: dict[str, Any], labels: dict[str, Any]) -> list[dict[str, Any]]:
    by_mark = {i["backlog"]: i for i in raw["issues"] if i["backlog"]}
    out: list[dict[str, Any]] = []
    for sid, s in (labels.get("stages") or {}).items():
        status = s.get("status", "pending")
        epic = by_mark.get(s.get("derive"))
        if epic:
            status = _status(epic["sub_done"], epic["sub_total"], state=epic["state"])
        out.append({"id": str(sid), "status": status, "what": s["what"]})
    return out


def build_plan(
    raw: dict[str, Any] | None, labels: dict[str, Any], today: dt.date
) -> dict[str, Any] | None:
    """Dict del capítulo 1 a partir de la respuesta reducida de GitHub y de `labels.yaml`."""
    if raw is None:
        return None
    ms = _milestones(raw, labels)
    league = dt.date(*LEAGUE_START)
    return {
        "source": {"kind": raw["source"], "fetched": raw.get("fetched")},
        "milestones": ms,
        "gates": [m for m in ms if m["gate"]],
        "rules": list(labels.get("rules") or []),
        "stages": _stages(raw, labels),
        "blocks": _epics(raw, labels),
        "league_start": league.isoformat(),
        "days_to_league": (league - today).days,
    }
