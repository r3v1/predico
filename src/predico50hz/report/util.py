# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Utilidades comunes: lectura tolerante de JSON/YAML, tamaños en disco y avisos."""

import json
import os
from pathlib import Path
from typing import Any

import yaml


def read_json(path: Path) -> dict[str, Any] | None:
    """Lee un JSON; devuelve None si falta o está corrupto (el capítulo muestra estado vacío)."""
    try:
        return json.loads(path.read_text("utf-8"))
    except OSError, ValueError:
        return None


def read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text("utf-8"))
    except OSError, yaml.YAMLError:
        return None


def dir_size(path: Path) -> int | None:
    """Bytes que ocupa un directorio (o fichero); None si no existe o no es accesible (NAS)."""
    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return None
        total = 0
        for base, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.stat(os.path.join(base, f)).st_size
                except OSError:
                    continue
        return total
    except OSError:
        return None


def has_delta(path: Path) -> bool:
    return (path / "_delta_log").is_dir()
