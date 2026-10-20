# pyright: basic
"""Orquestacion de una pasada: descarga -> recorte -> bronce (Icechunk) -> plata (Parquet)."""

import datetime as dt
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from predico50hz.ingest.ifs import paths
from predico50hz.ingest.ifs.bronze import BronzeRepo, PassCube
from predico50hz.ingest.ifs.crop import Cropped, Domain, decode_and_crop
from predico50hz.ingest.ifs.download import (
    ByteRange,
    Downloader,
    IndexEntry,
    plan_step,
    split_range,
)
from predico50hz.ingest.ifs.extract import (
    Point,
    extract_points,
    table_digest,
    write_silver,
)
from predico50hz.ingest.ifs.fields import (
    DYNAMIC_FIELDS,
    LEVELS,
    LSM,
    PRESSURE_VARS,
    SURFACE_VARS,
)

DEFAULT_STEPS: dict[int, tuple[int, ...]] = {
    0: tuple(range(18, 55, 3)),
    12: tuple(range(18, 55, 3)),
    6: tuple(range(12, 49, 3)),
    18: tuple(range(12, 49, 3)),
}


@dataclass
class PassMetrics:
    date: str
    cycle: int
    res: str = ""
    stream: str = ""
    steps: list[int] = field(default_factory=list)
    status: str = "ok"
    requests: int = 0
    mb_transferred: float = 0.0
    download_s: float = 0.0
    throughput_mb_s: float = 0.0
    n503: int = 0
    retries: int = 0
    decode_crop_s: float = 0.0
    bronze_write_s: float = 0.0
    silver_s: float = 0.0
    missing_fields: dict[str, list[str]] = field(default_factory=dict)
    silver_digest: str = ""
    silver_rows: int = 0
    concurrency: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Config:
    bronze: Path
    silver: Path
    points: list[Point]
    concurrency: int = 2
    max_gap: int = 0
    domain: Domain = field(default_factory=Domain)


def run_pass(  # noqa: PLR0912, PLR0913, PLR0915, PLR0917
    dl: Downloader,
    repo: BronzeRepo,
    cfg: Config,
    day: dt.date,
    cycle: int,
    steps: tuple[int, ...] | None = None,
) -> PassMetrics:
    steps = steps or DEFAULT_STEPS[cycle]
    m = PassMetrics(day.isoformat(), cycle, steps=list(steps), concurrency=cfg.concurrency)
    init = dt.datetime.combine(day, dt.time(cycle))
    layout = paths.resolve_layout(day, cycle, steps[0], dl.exists)
    if layout is None:
        m.status = "sin_datos"
        return m
    m.res, m.stream = layout.res, layout.stream
    st0 = (dl.stats.requests, dl.stats.bytes, dl.stats.n503, dl.stats.retries)

    # 1. descarga (en memoria): indices -> rangos fusionados -> bytes
    t0 = time.perf_counter()
    jobs: list[tuple[str, ByteRange]] = []
    need_lsm = not repo.has_lsm(layout.res)
    todo = [(s, False) for s in steps] + ([(0, True)] if need_lsm else [])
    for s, lsm_only in todo:
        entries: list[IndexEntry] | None = dl.read_index(paths.index_key(layout, day, cycle, s))
        if entries is None:
            m.missing_fields[str(s)] = ["index_no_existe"]
            continue
        if lsm_only:
            from predico50hz.ingest.ifs.download import merge_ranges  # noqa: PLC0415

            sel = [e for e in entries if e.matches(LSM)]
            rngs = merge_ranges(sel, cfg.max_gap)
            if not sel:
                m.missing_fields["lsm"] = ["lsm"]
        else:
            rngs, miss = plan_step(entries, include_lsm=False, max_gap=cfg.max_gap)
            if miss:
                m.missing_fields[str(s)] = miss
        jobs += [(paths.grib_key(layout, day, cycle, s), r) for r in rngs]
    blobs = dl.fetch_many(jobs)
    m.download_s = time.perf_counter() - t0
    m.requests = dl.stats.requests - st0[0]
    m.mb_transferred = (dl.stats.bytes - st0[1]) / 1e6
    m.n503 = dl.stats.n503 - st0[2]
    m.retries = dl.stats.retries - st0[3]
    m.throughput_mb_s = m.mb_transferred / m.download_s if m.download_s else 0.0

    # 2. decodificacion + recorte (los bytes globales se descartan al salir del bucle)
    t0 = time.perf_counter()
    grid: Cropped | None = None
    by_step: dict[int, dict[str, npt.NDArray[np.float32]]] = {}
    lsm: npt.NDArray[np.float32] | None = None
    for (_, rng), blob in zip(jobs, blobs, strict=True):
        for e, msg in split_range(rng, blob):
            c = decode_and_crop(msg, cfg.domain)
            grid = grid or c
            if e.matches(LSM):
                lsm = c.values
                continue
            f = next(f for f in DYNAMIC_FIELDS if e.matches(f))
            slot = by_step.setdefault(e.step, {})
            if f.levtype == "sfc":
                slot[f.var] = c.values
            else:
                slot[f"{f.var}@{f.levelist}"] = c.values
    del blobs, jobs
    if grid is None:
        m.status = "sin_campos"
        return m
    got = tuple(s for s in steps if s in by_step)
    nlat, nlon = grid.values.shape
    data: dict[str, npt.NDArray[np.float32]] = {}
    for v in SURFACE_VARS:
        arr = np.full((len(got), nlat, nlon), np.nan, dtype=np.float32)
        for k, s in enumerate(got):
            if v in by_step[s]:
                arr[k] = by_step[s][v]
        data[v] = arr
    for v in PRESSURE_VARS:
        arr = np.full((len(got), len(LEVELS), nlat, nlon), np.nan, dtype=np.float32)
        for k, s in enumerate(got):
            for q, lv in enumerate(LEVELS):
                if f"{v}@{lv}" in by_step[s]:
                    arr[k, q] = by_step[s][f"{v}@{lv}"]
        data[v] = arr
    cube = PassCube(layout.res, init, got, grid.lats, grid.lons, data, lsm)
    m.decode_crop_s = time.perf_counter() - t0

    # 3. bronce
    t0 = time.perf_counter()
    repo.write_pass(
        cube, f"ifs {layout.res} {layout.stream} {init:%Y-%m-%dT%HZ} steps {got[0]}-{got[-1]}"
    )
    m.bronze_write_s = time.perf_counter() - t0

    # 4. plata (desde bronce, que es la fuente de verdad)
    t0 = time.perf_counter()
    ds = repo.read_pass(layout.res, init)
    table = extract_points(ds, layout.res, cfg.points)
    write_silver(table, cfg.silver, init)
    m.silver_digest = table_digest(table)
    m.silver_rows = table.num_rows
    m.silver_s = time.perf_counter() - t0
    return m
