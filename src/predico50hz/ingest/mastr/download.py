# pyright: basic
"""Descarga del Gesamtdatenexport de MaStR (varios GB) al NAS, por tramos en paralelo y reanudable.

El servidor limita cada conexión a ~1 MB/s, pero admite `Range`: se baja en tramos de
`SEGMENT` bytes con varias conexiones y se escribe cada tramo en su posición con `pwrite`.
`<zip>.done` lista los tramos completos (uno por línea) para poder reanudar.

Licencia de los datos: DL-DE-BY-2.0 (Bundesnetzagentur / MaStR).
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from predico50hz.ingest.mastr.config import DOWNLOAD_PAGE, EXPORT_URL_PATTERN, RAW_ROOT

SEGMENT = 64 << 20
WORKERS = 8
BLOCK = 1 << 20


def latest_export_url(client: httpx.Client | None = None) -> tuple[str, str]:
    """URL y fecha (AAAAMMDD) del export completo más reciente anunciado en la web de MaStR."""
    c = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        html = c.get(DOWNLOAD_PAGE).raise_for_status().text
    finally:
        if client is None:
            c.close()
    found = re.search(EXPORT_URL_PATTERN, html)
    if found is None:
        raise RuntimeError("no se encontró el enlace del Gesamtdatenexport en la página de MaStR")
    return found.group(0), found.group(1)


def raw_dir(export_date: str, root: str | Path = RAW_ROOT) -> Path:
    return Path(root) / export_date


def _fetch_segment(url: str, fd: int, index: int, total: int, retries: int = 5) -> int:
    start = index * SEGMENT
    end = min(start + SEGMENT, total) - 1
    for attempt in range(retries):
        pos = start
        try:
            with (
                httpx.Client(timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True) as c,
                c.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as r,
            ):
                r.raise_for_status()
                for block in r.iter_bytes(BLOCK):
                    os.pwrite(fd, block, pos)
                    pos += len(block)
            if pos == end + 1:
                return index
        except httpx.HTTPError:
            if attempt == retries - 1:
                raise
    raise RuntimeError(f"tramo {index} incompleto")


def download_export(url: str, dest: Path, workers: int = WORKERS) -> Path:
    """Descarga `url` en `dest`; reanuda con los tramos de `<dest>.done` y el prefijo existente."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, follow_redirects=True) as c:
        total = int(c.head(url).raise_for_status().headers["Content-Length"])
    n = -(-total // SEGMENT)
    done_file = Path(f"{dest}.done")
    if done_file.exists():
        done = {int(x) for x in done_file.read_text().split()}
    else:  # descarga secuencial previa (p. ej. curl): vale el prefijo completo
        have = dest.stat().st_size if dest.exists() else 0
        done = {i for i in range(n) if (i + 1) * SEGMENT <= have}
    todo = [i for i in range(n) if i not in done]
    if todo:
        fd = os.open(dest, os.O_RDWR | os.O_CREAT)
        try:
            os.ftruncate(fd, total)
            done_file.write_text("".join(f"{i}\n" for i in sorted(done)))
            with ThreadPoolExecutor(workers) as pool, done_file.open("a") as log:
                for i in pool.map(lambda j: _fetch_segment(url, fd, j, total), todo):
                    log.write(f"{i}\n")
                    log.flush()
        finally:
            os.close(fd)
    if dest.stat().st_size != total:
        raise RuntimeError("tamaño final distinto del anunciado")
    return dest
