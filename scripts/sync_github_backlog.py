"""Siembra en GitHub el backlog de `.github/backlog.yaml`.

Crea etiquetas, milestones, issues, sub-issues y relaciones «Blocked by». Idempotente: reconoce cada
issue por la marca `<!-- backlog:<id> -->` de su cuerpo y solo crea lo que falta; nunca edita,
cierra ni borra nada que ya exista. Usa la CLI `gh` ya autenticada.

    uv run python scripts/sync_github_backlog.py --dry-run
    uv run python scripts/sync_github_backlog.py
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

BACKLOG = Path(__file__).resolve().parents[1] / ".github" / "backlog.yaml"
MARK_RE = re.compile(r"<!-- backlog:([\w.\-]+) -->")


def gh(*args: str, payload: dict[str, Any] | None = None) -> Any:
    cmd = ["gh", "api", *args]
    if payload is not None:
        cmd += ["--input", "-"]
    out = subprocess.run(
        cmd,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)}: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def render_body(item: dict[str, Any]) -> str:
    parts = ["## Contexto", "", item["context"].strip(), ""]
    if item.get("tasks"):
        parts += ["## Qué hay que hacer", ""]
        parts += [f"- [ ] {t}" for t in item["tasks"]] + [""]
    if item.get("acceptance"):
        parts += ["## Criterio de aceptación", ""]
        parts += [f"- {a}" for a in item["acceptance"]] + [""]
    if item.get("refs"):
        parts += ["## Referencias", ""]
        parts += [f"- {r}" for r in item["refs"]] + [""]
    parts.append(f"<!-- backlog:{item['id']} -->")
    return "\n".join(parts)


def check(backlog: dict[str, Any]) -> None:
    ids = [i["id"] for i in backlog["issues"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        sys.exit(f"ids repetidos: {sorted(dup)}")
    known = set(ids)
    for i in backlog["issues"]:
        if i["milestone"] not in backlog["milestones"]:
            sys.exit(f"{i['id']}: milestone desconocido {i['milestone']}")
        for lab in i.get("labels", []):
            if lab not in backlog["labels"]:
                sys.exit(f"{i['id']}: etiqueta desconocida {lab}")
        for ref in [i.get("parent"), *i.get("blocked_by", [])]:
            if ref and ref not in known:
                sys.exit(f"{i['id']}: referencia desconocida {ref}")
    # sin ciclos en «Blocked by»
    deps = {i["id"]: i.get("blocked_by", []) for i in backlog["issues"]}
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 1:
            sys.exit(f"ciclo en blocked_by: {' → '.join([*path, n])}")
        if state.get(n) == 2:  # noqa: PLR2004
            return
        state[n] = 1
        for d in deps[n]:
            visit(d, [*path, n])
        state[n] = 2

    for n in deps:
        visit(n, [])


def sync_labels(repo: str, labels: dict[str, Any]) -> None:
    have = {lab["name"] for lab in gh(f"repos/{repo}/labels", "--paginate")}
    for name, spec in labels.items():
        if name not in have:
            gh(f"repos/{repo}/labels", payload={"name": name, **spec})
            print(f"etiqueta + {name}")


def sync_milestones(repo: str, specs: dict[str, Any]) -> dict[str, int]:
    """Clave del YAML → número del milestone."""
    have = {m["title"]: m["number"] for m in gh(f"repos/{repo}/milestones?state=all", "--paginate")}
    for spec in specs.values():
        if spec["title"] not in have:
            body: dict[str, Any] = {"title": spec["title"], "description": spec["description"]}
            if spec.get("due"):
                body["due_on"] = f"{spec['due']}T12:00:00Z"
            have[spec["title"]] = gh(f"repos/{repo}/milestones", payload=body)["number"]
            print(f"milestone + {spec['title']}")
    return {key: have[spec["title"]] for key, spec in specs.items()}


def sync_issues(
    repo: str, items: list[dict[str, Any]], ms_number: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """Id del YAML → issue de GitHub (los que faltan se crean)."""
    existing: dict[str, dict[str, Any]] = {}
    for iss in gh(f"repos/{repo}/issues?state=all&per_page=100", "--paginate"):
        m = MARK_RE.search(iss.get("body") or "")
        if m and "pull_request" not in iss:
            existing[m.group(1)] = iss
    for item in items:
        if item["id"] in existing:
            continue
        iss = gh(
            f"repos/{repo}/issues",
            payload={
                "title": item["title"],
                "body": render_body(item),
                "milestone": ms_number[item["milestone"]],
                "labels": item.get("labels", []),
            },
        )
        existing[item["id"]] = iss
        print(f"issue + #{iss['number']} {item['title']}")
        time.sleep(1)  # límite secundario de creación de contenido
    return existing


def sync_relations(repo: str, items: list[dict[str, Any]], issues: dict[str, Any]) -> None:
    for item in items:
        num = issues[item["id"]]["number"]
        if item.get("parent"):
            parent = issues[item["parent"]]["number"]
            subs = gh(f"repos/{repo}/issues/{parent}/sub_issues", "--paginate")
            if num not in {s["number"] for s in subs}:
                payload = {"sub_issue_id": issues[item["id"]]["id"]}
                gh(f"repos/{repo}/issues/{parent}/sub_issues", payload=payload)
                print(f"sub-issue #{num} ⊂ #{parent}")
        if item.get("blocked_by"):
            url = f"repos/{repo}/issues/{num}/dependencies/blocked_by"
            have = {d["number"] for d in gh(url, "--paginate")}
            for dep in item["blocked_by"]:
                if issues[dep]["number"] not in have:
                    gh(url, payload={"issue_id": issues[dep]["id"]})
                    print(f"#{num} bloqueado por #{issues[dep]['number']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="valida y muestra lo que crearía")
    args = ap.parse_args()
    backlog = yaml.safe_load(BACKLOG.read_text())
    check(backlog)
    repo, items = backlog["repo"], backlog["issues"]
    n_ms, n_lab = len(backlog["milestones"]), len(backlog["labels"])
    print(f"{len(items)} issues, {n_ms} milestones, {n_lab} etiquetas")
    if args.dry_run:
        for i in items:
            extra = f" ⊂ {i['parent']}" if i.get("parent") else ""
            blk = f" ⟵ {', '.join(i['blocked_by'])}" if i.get("blocked_by") else ""
            print(f"  [{i['milestone']}] {i['id']}: {i['title']}{extra}{blk}")
        return 0
    sync_labels(repo, backlog["labels"])
    ms_number = sync_milestones(repo, backlog["milestones"])
    issues = sync_issues(repo, items, ms_number)
    sync_relations(repo, items, issues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
