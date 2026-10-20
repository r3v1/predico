# pyright: basic
"""Una pasada: lectura lazy -> bronce (Icechunk propio) -> plata (Parquet), con metricas."""

import datetime as dt
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from predico50hz.ingest.dynamical.bronze import BronzeStore
from predico50hz.ingest.dynamical.config import DOMAIN, VARIABLES, Domain
from predico50hz.ingest.dynamical.silver import (
    build_table,
    table_digest,
    write_silver,
)
from predico50hz.ingest.dynamical.source import Cube, Source, read_cube
from predico50hz.ingest.ifs.extract import Point


def net_rx_bytes() -> int:
    """Bytes recibidos por la interfaz de la ruta por defecto (suma de /proc/net/dev)."""
    iface = None
    for line in Path("/proc/net/route").read_text().splitlines()[1:]:
        f = line.split()
        if f[1] == "00000000":
            iface = f[0]
            break
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, rest = line.split(":", 1)
        if name.strip() == iface:
            return int(rest.split()[0])
    return 0


@dataclass
class PassMetrics:
    init_date: str
    status: str = "ok"
    read_s: float = 0.0
    bronze_s: float = 0.0
    silver_s: float = 0.0
    mb_transferred: float = 0.0
    n_nan_fraction: float = 0.0
    bronze_commit: str | None = None
    bronze_digest: str = ""
    silver_digest: str = ""
    silver_bytes: int = 0
    workers: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Config:
    bronze: BronzeStore
    silver: Path
    points: list[Point]
    workers: int = 5
    domain: Domain = DOMAIN
    variables: tuple[str, ...] = VARIABLES


@dataclass(frozen=True)
class ReadResult:
    cube: Cube
    read_s: float
    mb: float


def read_stage(src: Source, cfg: Config, day: dt.date) -> ReadResult:
    """Lectura lazy de una pasada: (cubo, segundos, MB de red durante la lectura).

    Los MB son el delta de /proc/net/dev: solo son atribuibles a la pasada si no hay otra
    lectura concurrente en el proceso (con varias pasadas en paralelo se mezclan).
    """
    rx0 = net_rx_bytes()
    t = time.perf_counter()
    cube = read_cube(
        src, np.datetime64(day.isoformat(), "ns"), cfg.variables, cfg.domain, cfg.workers
    )
    return ReadResult(cube, time.perf_counter() - t, (net_rx_bytes() - rx0) / 1e6)


def write_stage(rr: ReadResult, src: Source, cfg: Config, day: dt.date) -> PassMetrics:
    cube = rr.cube
    m = PassMetrics(day.isoformat(), workers=cfg.workers, read_s=rr.read_s, mb_transferred=rr.mb)
    m.n_nan_fraction = float(np.mean([np.isnan(a).mean() for a in cube.data.values()]))

    t = time.perf_counter()
    msg = (
        f"ifs-ens init {day.isoformat()} 00Z | dynamical v{src.dataset_version} "
        f"snapshot {src.snapshot_id}"
    )
    m.bronze_commit = cfg.bronze.write_pass(cube, day, src, msg, cfg.variables)
    m.bronze_s = time.perf_counter() - t

    t = time.perf_counter()
    info = {"source_href": src.href, "source_dataset_version": src.dataset_version}
    table = build_table(cube, cfg.points, cube.init, info)
    out = write_silver(table, cfg.silver, day.isoformat())
    m.silver_s = time.perf_counter() - t
    m.silver_digest = table_digest(table)
    m.silver_bytes = out.stat().st_size
    m.bronze_digest = cfg.bronze.digest(day, cfg.variables)
    return m


def run_pass(src: Source, cfg: Config, day: dt.date, skip_existing: bool = False) -> PassMetrics:
    if skip_existing and cfg.bronze.is_ingested(day):
        return PassMetrics(day.isoformat(), status="skipped", workers=cfg.workers)
    return write_stage(read_stage(src, cfg, day), src, cfg, day)


__all__ = [
    "Config",
    "PassMetrics",
    "ReadResult",
    "net_rx_bytes",
    "read_stage",
    "run_pass",
    "write_stage",
]
