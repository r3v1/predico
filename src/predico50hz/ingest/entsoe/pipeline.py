# ruff: noqa: PLR0913, PLR0917
"""Descarga a bronce de un dataset: troceado, reanudación y métricas."""

import datetime as dt
import time
from pathlib import Path

from predico50hz.ingest.entsoe.bronze import (
    chunk_name,
    run_dir,
    stamp,
    write_chunk,
    write_run_info,
)
from predico50hz.ingest.entsoe.client import EntsoeClient, EntsoeError, NoDataError
from predico50hz.ingest.entsoe.datasets import Dataset, request_params, windows

FALLBACK_SUFFIX = "-fallback"  # el trozo vino de la zona DE-LU y no del área de control


def default_end(ds: Dataset, today: dt.date) -> dt.date:
    if ds.chunk == "year":
        return dt.date(today.year + 1, 1, 1)
    return today + dt.timedelta(days=2)  # day-ahead de mañana ya publicado


def _done(root: Path, ds: Dataset, fetched: str, variant: str, a: dt.datetime, b: dt.datetime):
    d = run_dir(root, ds.name, fetched)
    return any(
        (d / chunk_name(variant + s, a, b, ext)).exists()
        for s in ("", FALLBACK_SUFFIX)
        for ext in ("xml.gz", "zip", "nodata")
    )


def fetch_dataset(
    client: EntsoeClient,
    ds: Dataset,
    start: dt.date,
    end: dt.date,
    root: Path,
    fetched: str | None = None,
) -> dict[str, object]:
    """Descarga `start`..`end` a `fetched_at=<fetched>`; si ya existe, salta lo descargado."""
    fetched = fetched or stamp(dt.datetime.now(dt.UTC))
    t0 = time.perf_counter()
    req0, ret0, bytes0 = client.requests, client.retries, client.bytes_raw
    n_chunks = n_skipped = n_nodata = stored = 0
    errors: list[str] = []
    for a, b in windows(max(start, ds.start), end, ds.chunk):
        for variant in ds.variants:
            if _done(root, ds, fetched, variant, a, b):
                n_skipped += 1
                continue
            raw, name = None, variant
            try:
                try:
                    raw = client.fetch(request_params(ds, variant), a, b)
                except NoDataError:
                    if ds.fallback_area is None:
                        raise
                    raw = client.fetch(request_params(ds, variant, ds.fallback_area), a, b)
                    name = variant + FALLBACK_SUFFIX
            except NoDataError:
                n_nodata += 1
            except EntsoeError as e:
                errors.append(f"{variant} {a:%Y-%m-%d}..{b:%Y-%m-%d}: {e}")
                continue
            path = write_chunk(root, ds.name, fetched, name, a, b, raw)
            n_chunks += 1
            stored += path.stat().st_size
    info: dict[str, object] = {
        "dataset": ds.name,
        "fetched_at": fetched,
        "start": start,
        "end": end,
        "chunks_written": n_chunks,
        "chunks_skipped_resume": n_skipped,
        "chunks_nodata": n_nodata,
        "requests": client.requests - req0,
        "retries": client.retries - ret0,
        "bytes_raw": client.bytes_raw - bytes0,
        "bytes_stored": stored,
        "seconds": round(time.perf_counter() - t0, 1),
        "errors": errors,
    }
    write_run_info(root, ds.name, fetched, info)
    return info
