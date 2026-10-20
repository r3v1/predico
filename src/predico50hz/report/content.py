# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Textos editados a mano en `report/`: novedades, hallazgos, fuentes, glosario y etiquetas."""

from typing import Any

from predico50hz.report.paths import Paths
from predico50hz.report.util import read_yaml


def _list(doc: Any, key: str) -> list[dict[str, Any]]:
    items = doc.get(key) if isinstance(doc, dict) else doc
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def load(paths: Paths) -> dict[str, Any]:
    d = paths.content
    sources = read_yaml(d / "sources.yaml") or {}
    labels = read_yaml(d / "labels.yaml") or {}
    return {
        "changelog": _list(read_yaml(d / "changelog.yaml"), "entries"),
        "insights": _list(read_yaml(d / "insights.yaml"), "insights"),
        "glossary": _list(read_yaml(d / "glossary.yaml"), "terms"),
        "sources": _list(sources, "sources"),
        "places": _list(sources, "places"),
        "routes": _list(sources, "routes"),
        "journey": (sources.get("journey") if isinstance(sources, dict) else None) or {},
        "clock": (sources.get("clock") if isinstance(sources, dict) else None) or {},
        "model": (read_yaml(d / "model.yaml") or {}),
        "variables": read_yaml(d / "variables.yaml") or {},
        "labels": labels if isinstance(labels, dict) else {},
    }
