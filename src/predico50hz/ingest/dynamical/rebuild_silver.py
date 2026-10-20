# pyright: basic, reportArgumentType=false, reportIndexIssue=false
"""Reconstruye la plata desde el bronce (solo lectura, sin tocar dynamical).

`uv run python -m predico50hz.ingest.dynamical.rebuild_silver --start 2025-09-01 \
    --end 2026-10-15 --centroids config/centroids.yaml --silver data/silver/ifs_ens_clusters`

Reutiliza `build_table` y `write_silver` de la ingesta (bilineal por miembro, velocidad por
miembro, agregados del ensemble). Es idempotente: cada Parquet guarda en sus metadatos el SHA-256
del fichero de centroides y el hash de los valores de bronce de la pasada, y una pasada cuyo
fichero ya los lleva iguales se omite (`--force` la regenera). Las pasadas no ingeridas se
saltan. Las pasadas son independientes y se calculan en hilos (`--workers`).
"""

import argparse
import datetime as dt
import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import icechunk
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import zarr

from predico50hz.ingest.dynamical.bronze import BRANCH, init_pos
from predico50hz.ingest.dynamical.config import (
    DEFAULT_BRONZE,
    DEFAULT_CENTROIDS,
    DEFAULT_SILVER,
    LEAD_HOURS,
    VARIABLES,
)
from predico50hz.ingest.dynamical.silver import build_table, silver_path, write_silver
from predico50hz.ingest.dynamical.source import Cube
from predico50hz.ingest.ifs.extract import Point, load_points

META_CENTROIDS = "centroids_file"
META_CENTROIDS_SHA = "centroids_sha256"
META_SNAPSHOT = "bronze_snapshot_id"
META_BRONZE_DIGEST = "bronze_digest"


@dataclass
class RebuildResult:
    init_date: str
    status: str  # "ok" | "unchanged" | "not_ingested"
    seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def open_bronze_ro(path: str | Path, snapshot_id: str | None = None) -> tuple[zarr.Group, str]:
    """Sesión de solo lectura (rama `main` o un snapshot fijo); no crea ni escribe nada."""
    repo = icechunk.Repository.open(icechunk.local_filesystem_storage(str(path)))
    session = (
        repo.readonly_session(snapshot_id=snapshot_id)
        if snapshot_id
        else repo.readonly_session(branch=BRANCH)
    )
    return zarr.open_group(session.store, mode="r"), session.snapshot_id


def bronze_cube(root: zarr.Group, day: dt.date) -> Cube:
    """Cubo de una pasada de bronce: arrays (lead, member, lat, lon) float32, latitud ascendente."""
    i = init_pos(day)
    data = {v: np.ascontiguousarray(root[v][i], dtype=np.float32) for v in VARIABLES if v in root}
    return Cube(
        np.datetime64(day.isoformat(), "ns"),
        LEAD_HOURS,
        np.asarray(root["latitude"][:], dtype=np.float64),
        np.asarray(root["longitude"][:], dtype=np.float64),
        data,
    )


def bronze_pass_digest(cube: Cube) -> str:
    """SHA-256 de los valores de la pasada; mismo cálculo que `BronzeStore.digest`."""
    h = hashlib.sha256()
    for v in VARIABLES:
        h.update(v.encode())
        h.update(np.ascontiguousarray(cube.data[v], dtype=np.float32).tobytes())
    return h.hexdigest()


def _file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def silver_meta(path: Path) -> dict[str, str]:
    """Metadatos Parquet del fichero de plata (vacío si no existe o es ilegible)."""
    try:
        raw = pq.read_schema(path).metadata or {}
    except OSError, pa.ArrowInvalid:
        return {}
    return {k.decode(): v.decode() for k, v in raw.items()}


def rebuild_pass(  # noqa: PLR0913, PLR0917
    root: zarr.Group,
    day: dt.date,
    points: list[Point],
    silver: Path,
    run_info: dict[str, str],
    force: bool = False,
) -> RebuildResult:
    t = time.perf_counter()
    if not root["ingested"][init_pos(day)]:
        return RebuildResult(day.isoformat(), "not_ingested")
    cube = bronze_cube(root, day)
    digest = bronze_pass_digest(cube)
    info = {**run_info, META_BRONZE_DIGEST: digest}
    old = silver_meta(silver_path(silver, day.isoformat()))
    if not force and all(old.get(k) == info[k] for k in (META_CENTROIDS_SHA, META_BRONZE_DIGEST)):
        return RebuildResult(day.isoformat(), "unchanged", time.perf_counter() - t)
    table = build_table(cube, points, cube.init, info)
    write_silver(table, silver, day.isoformat())
    return RebuildResult(day.isoformat(), "ok", time.perf_counter() - t)


def rebuild(  # noqa: PLR0913, PLR0917
    start: dt.date,
    end: dt.date,
    bronze: str | Path = DEFAULT_BRONZE,
    silver: str | Path = DEFAULT_SILVER,
    centroids: str | Path = DEFAULT_CENTROIDS,
    workers: int = 4,
    force: bool = False,
    snapshot_id: str | None = None,
) -> list[RebuildResult]:
    if end < start:
        raise ValueError("--end anterior a --start")
    _ = (init_pos(start), init_pos(end))
    points = load_points(centroids)
    root, snapshot = open_bronze_ro(bronze, snapshot_id)
    run_info = {
        "source_href": str(root.attrs.get("source_href", "")),
        "source_dataset_version": str(root.attrs.get("source_dataset_version", "")),
        META_CENTROIDS: str(centroids),
        META_CENTROIDS_SHA: _file_sha(centroids),
        META_SNAPSHOT: snapshot,
    }
    days = [start + dt.timedelta(days=k) for k in range((end - start).days + 1)]
    out: list[RebuildResult] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = [
            ex.submit(rebuild_pass, root, d, points, Path(silver), run_info, force) for d in days
        ]
        for k, fut in enumerate(futures, 1):
            out.append(fut.result())
            if k % 25 == 0 or k == len(days):
                print(f"  {k}/{len(days)} pasadas", file=sys.stderr, flush=True)
    return out


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_date, required=True)
    ap.add_argument("--end", type=_date, required=True)
    ap.add_argument("--bronze", default=DEFAULT_BRONZE)
    ap.add_argument("--silver", default=DEFAULT_SILVER)
    ap.add_argument("--centroids", default=DEFAULT_CENTROIDS)
    ap.add_argument("--workers", type=int, default=4, help="pasadas calculadas a la vez")
    ap.add_argument("--snapshot-id", help="fija el snapshot de bronce (por defecto, el de main)")
    ap.add_argument("--force", action="store_true", help="regenera aunque los hashes coincidan")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    res = rebuild(
        args.start,
        args.end,
        args.bronze,
        args.silver,
        args.centroids,
        args.workers,
        args.force,
        args.snapshot_id,
    )
    counts: dict[str, int] = {}
    for r in res:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"{counts} en {time.perf_counter() - t0:.1f} s -> {args.silver}")


if __name__ == "__main__":
    main()
