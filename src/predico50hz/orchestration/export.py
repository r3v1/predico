"""Exportación de la operación: un `.tar.gz` con lo necesario para revisar G0 desde fuera.

Contiene `ops/` (registro de ejecuciones y tabla de disponibilidad, sin locks ni exportaciones
previas), `status.json`, `publication_times.json`, los informes de validación recientes, el final
del log y `meta.json`. Nunca incluye `.env`; además, todo texto pasa por `redact`.
"""

import datetime as dt
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any

from predico50hz.ingest.entsoe.redact import redact
from predico50hz.orchestration.settings import OpsSettings

_TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".md"}
_IFS_REPORT = re.compile(r"^ifs_ens_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json$")
_SKIP_DIRS = {"locks"}


def export_path(settings: OpsSettings, today: dt.date) -> Path:
    return settings.ops_path / f"export_{today.isoformat()}.tar.gz"


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mtime: float) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(mtime), 0o644
    tar.addfile(info, io.BytesIO(data))


def _add_file(tar: tarfile.TarFile, src: Path, name: str) -> None:
    if src.suffix in _TEXT_SUFFIXES:
        text = redact(src.read_text(encoding="utf-8", errors="replace"))
        _add_bytes(tar, name, text.encode(), src.stat().st_mtime)
    else:
        tar.add(src, arcname=name, recursive=False)


def tail_lines(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:]) + "\n"


def _recent_reports(settings: OpsSettings, today: dt.date, days: int) -> list[Path]:
    root = settings.validation_path
    if not root.exists():
        return []
    cutoff = today - dt.timedelta(days=days)
    out: list[Path] = []
    for p in sorted(root.glob("*.json")):
        m = _IFS_REPORT.match(p.name)
        if m:
            if dt.date.fromisoformat(m[2]) >= cutoff:
                out.append(p)
        elif p.name.startswith(("entsoe_", "gold_")):
            out.append(p)
    return out


def build_export(  # noqa: PLR0913, PLR0917
    settings: OpsSettings,
    status_report: dict[str, Any],
    now: dt.datetime,
    days: int = 7,
    log_lines: int = 2000,
    out: Path | None = None,
) -> Path:
    """Escribe el tar.gz (de forma atómica) y devuelve su ruta."""
    out = out or export_path(settings, now.date())
    out.parent.mkdir(parents=True, exist_ok=True)
    root = f"predico-export-{now.date().isoformat()}"
    tmp = out.with_name(out.name + ".tmp")
    ts = now.timestamp()
    ops = settings.ops_path
    with tarfile.open(tmp, "w:gz") as tar:
        meta = {
            "generated_at": now.isoformat(timespec="seconds"),
            "host": settings.host,
            "days": days,
            "log_lines": log_lines,
        }
        _add_bytes(tar, f"{root}/meta.json", json.dumps(meta, indent=2).encode(), ts)
        status_text = redact(json.dumps(status_report, indent=2, ensure_ascii=False))
        _add_bytes(tar, f"{root}/status.json", status_text.encode(), ts)
        for p in sorted(ops.rglob("*")):
            rel = p.relative_to(ops)
            if (
                not p.is_file()
                or rel.parts[0] in _SKIP_DIRS
                or rel.name.startswith("export_")
                or rel.name.startswith(settings.log_path.name)
            ):
                continue
            _add_file(tar, p, f"{root}/ops/{rel}")
        log = redact(tail_lines(settings.log_path, log_lines))
        _add_bytes(tar, f"{root}/logs/ops.log.tail", log.encode(), ts)
        for p in _recent_reports(settings, now.date(), days):
            _add_file(tar, p, f"{root}/validation/{p.name}")
    tmp.replace(out)
    return out
