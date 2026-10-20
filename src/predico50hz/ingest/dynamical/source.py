# pyright: basic
"""Lectura lazy de dynamical.org (Icechunk/Zarr) recortada al dominio."""

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import icechunk
import numpy as np
import numpy.typing as npt
import pystac
import xarray as xr

from predico50hz.ingest.dynamical.config import (
    DATASET_ID,
    LEAD_HOURS,
    PINNED_VERSION,
    STAC_ASSET,
    STAC_URL,
    Domain,
)


@dataclass(frozen=True)
class Source:
    ds: xr.Dataset
    href: str
    snapshot_id: str
    dataset_version: str


def resolve_href() -> str:
    cat = pystac.Catalog.from_file(STAC_URL)
    return cat.get_child(DATASET_ID).assets[STAC_ASSET].href  # pyright: ignore


def open_source(href: str | None = None, snapshot_id: str | None = None) -> Source:
    """Abre el dataset (lazy). Falla si la version no es la fijada."""
    href = href or resolve_href()
    repo = icechunk.Repository.open(icechunk.http_storage(href))
    session = (
        repo.readonly_session(snapshot_id=snapshot_id)
        if snapshot_id
        else repo.readonly_session("main")
    )
    ds = xr.open_zarr(session.store, chunks=None)
    version = str(ds.attrs.get("dataset_version"))
    if version != PINNED_VERSION:
        raise RuntimeError(f"dataset_version {version} != fijada {PINNED_VERSION}")
    return Source(ds, href, session.snapshot_id, version)


def axis_slice(axis: npt.NDArray[np.float64], lo: float, hi: float) -> slice:
    """Indices contiguos de los puntos de `axis` (ascendente o descendente) dentro de [lo, hi]."""
    idx = np.flatnonzero((axis >= lo - 1e-9) & (axis <= hi + 1e-9))
    if idx.size == 0:
        raise ValueError(f"ningun punto de la malla en [{lo}, {hi}]")
    if not np.all(np.diff(idx) == 1):
        raise ValueError("el dominio no es contiguo en la malla")
    return slice(int(idx[0]), int(idx[-1]) + 1)


def lead_slice(lead_time: npt.NDArray[np.timedelta64]) -> slice:
    """Slice contiguo con exactamente LEAD_HOURS (comprueba los valores, no solo la longitud)."""
    hours = (lead_time / np.timedelta64(1, "h")).astype(np.int64)
    first = int(np.flatnonzero(hours == LEAD_HOURS[0])[0])
    sl = slice(first, first + len(LEAD_HOURS))
    if tuple(hours[sl].tolist()) != LEAD_HOURS:
        raise ValueError(f"pasos {hours[sl].tolist()} != {list(LEAD_HOURS)}")
    return sl


def init_index(init_times: npt.NDArray[np.datetime64], init: np.datetime64) -> int:
    hit = np.flatnonzero(init_times == init)
    if hit.size == 0:
        raise KeyError(f"init_time {init} no esta en el origen")
    return int(hit[0])


@dataclass(frozen=True)
class Cube:
    """Pasada recortada: arrays (lead, member, lat, lon) float32 con latitud ASCENDENTE."""

    init: np.datetime64
    lead_hours: tuple[int, ...]
    lats: npt.NDArray[np.float64]
    lons: npt.NDArray[np.float64]
    data: dict[str, npt.NDArray[np.float32]]


def read_cube(
    src: Source,
    init: np.datetime64,
    variables: Sequence[str],
    domain: Domain,
    workers: int = 5,
) -> Cube:
    ds = src.ds
    i = init_index(ds["init_time"].values, init)
    ls = lead_slice(ds["lead_time"].values)
    ys = axis_slice(ds["latitude"].values, domain.lat_min, domain.lat_max)
    xs = axis_slice(ds["longitude"].values, domain.lon_min, domain.lon_max)
    lats = ds["latitude"].values[ys]
    lons = ds["longitude"].values[xs]

    def one(v: str) -> npt.NDArray[np.float32]:
        # el slice de latitud es descendente en origen: se invierte a ascendente
        arr = ds[v].isel(init_time=i, lead_time=ls, latitude=ys, longitude=xs).values
        return np.ascontiguousarray(arr[..., ::-1, :], dtype=np.float32)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        arrays = list(ex.map(one, variables))
    return Cube(
        init, LEAD_HOURS, lats[::-1].copy(), lons, dict(zip(variables, arrays, strict=True))
    )


def timed_read(
    src: Source, init: np.datetime64, variables: Sequence[str], domain: Domain, workers: int
) -> tuple[Cube, float]:
    t = time.perf_counter()
    cube = read_cube(src, init, variables, domain, workers)
    return cube, time.perf_counter() - t
