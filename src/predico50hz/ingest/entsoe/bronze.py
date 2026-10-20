# ruff: noqa: PLR0913, PLR0917
"""Capa bronce de ENTSO-E: respuesta cruda (XML gzip o ZIP) por descarga.

Ruta: `<raíz>/<dataset>/fetched_at=<UTC AAAAMMDDTHHMMSSZ>/<variante>_<inicio>_<fin>.xml.gz`.
Una ventana sin datos deja `<...>.nodata` vacío (así una reanudación no la repite). Cada
ejecución es una versión nueva: nada se sobrescribe, y las revisiones salen de comparar
versiones.
"""

import datetime as dt
import gzip
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

STAMP_FMT = "%Y%m%dT%H%M%SZ"
_NAME_RE = re.compile(
    r"^(?P<variant>[^_]*)_(?P<a>\d{8})_(?P<b>\d{8})\.(?P<ext>xml\.gz|zip|nodata)$"
)


@dataclass(frozen=True)
class Chunk:
    fetched_at: dt.datetime
    variant: str
    start: dt.date
    end: dt.date
    path: Path

    @property
    def nodata(self) -> bool:
        return self.path.suffix == ".nodata"


def stamp(now: dt.datetime) -> str:
    return now.astimezone(dt.UTC).strftime(STAMP_FMT)


def parse_stamp(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, STAMP_FMT).replace(tzinfo=dt.UTC)


def chunk_name(variant: str, start: dt.datetime, end: dt.datetime, ext: str) -> str:
    return f"{variant}_{start:%Y%m%d}_{end:%Y%m%d}.{ext}"


def run_dir(root: Path, dataset: str, fetched: str) -> Path:
    return root / dataset / f"fetched_at={fetched}"


def write_chunk(
    root: Path,
    dataset: str,
    fetched: str,
    variant: str,
    start: dt.datetime,
    end: dt.datetime,
    raw: bytes | None,
) -> Path:
    """Guarda la respuesta cruda (`None` = sin datos) de forma atómica."""
    d = run_dir(root, dataset, fetched)
    d.mkdir(parents=True, exist_ok=True)
    if raw is None:
        path, payload = d / chunk_name(variant, start, end, "nodata"), b""
    elif raw[:2] == b"PK":
        path, payload = d / chunk_name(variant, start, end, "zip"), raw
    else:
        path = d / chunk_name(variant, start, end, "xml.gz")
        payload = gzip.compress(raw, compresslevel=6, mtime=0)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)
    return path


def write_run_info(root: Path, dataset: str, fetched: str, info: dict[str, object]) -> None:
    d = run_dir(root, dataset, fetched)
    d.mkdir(parents=True, exist_ok=True)
    (d / "_run.json").write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str) + "\n")


def list_chunks(root: Path, dataset: str) -> Iterator[Chunk]:
    """Todos los trozos del dataset, de la descarga más antigua a la más reciente."""
    for d in sorted((root / dataset).glob("fetched_at=*")):
        fetched = parse_stamp(d.name.removeprefix("fetched_at="))
        for p in sorted(d.iterdir()):
            if m := _NAME_RE.match(p.name):
                yield Chunk(
                    fetched,
                    m["variant"],
                    dt.datetime.strptime(m["a"], "%Y%m%d").date(),
                    dt.datetime.strptime(m["b"], "%Y%m%d").date(),
                    p,
                )


def read_chunk(chunk: Chunk) -> bytes:
    """Bytes crudos (XML o ZIP) del trozo; vacío si no hay datos."""
    if chunk.nodata:
        return b""
    data = chunk.path.read_bytes()
    return gzip.decompress(data) if chunk.path.suffix == ".gz" else data
