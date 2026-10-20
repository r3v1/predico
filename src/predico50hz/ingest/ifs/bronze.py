# pyright: basic, reportArgumentType=false, reportCallIssue=false
"""Capa bronce: recorte original en Zarr v3 sobre un repositorio Icechunk.

Un grupo por resolucion (`0p25`, `0p4-beta`) porque la malla cambia con ella. Cada
grupo tiene un eje `init_time` prerreservado (cada 6 h, 2023-2028) y un eje `step`
(0..90 h cada 3 h), de modo que escribir una pasada es sobrescribir una region fija:
idempotente y valido en cualquier orden (hacia atras en la Etapa 3, 06Z, etc.).
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import icechunk
import numpy as np
import numpy.typing as npt
import xarray as xr
import zarr
from zarr.errors import GroupNotFoundError

from predico50hz.ingest.ifs.fields import LEVELS, PRESSURE_VARS, SURFACE_VARS

T0 = dt.datetime(2023, 1, 1)
T_END = dt.datetime(2029, 1, 1)
INIT_FREQ_H = 6
STEPS_H: tuple[int, ...] = tuple(range(0, 91, 3))
BRANCH = "main"
_ATTR_UNITS_INIT = "hours since 2023-01-01 00:00:00"


def init_index(init: dt.datetime) -> int:
    hours = (init - T0).total_seconds() / 3600
    if hours < 0 or init >= T_END or hours % INIT_FREQ_H:
        raise ValueError(f"init_time fuera del eje bronce: {init}")
    return int(hours // INIT_FREQ_H)


def _n_init() -> int:
    return int((T_END - T0).total_seconds() // 3600 // INIT_FREQ_H)


@dataclass(frozen=True)
class PassCube:
    """Recorte de una pasada: variables con dims (step,[level],lat,lon)."""

    res: str
    init: dt.datetime
    steps: tuple[int, ...]
    lats: npt.NDArray[np.float64]
    lons: npt.NDArray[np.float64]
    data: dict[str, npt.NDArray[np.float32]]  # claves de SURFACE_VARS / PRESSURE_VARS
    lsm: npt.NDArray[np.float32] | None = None


class BronzeRepo:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        storage = icechunk.local_filesystem_storage(str(self.path))
        self.repo = icechunk.Repository.open_or_create(storage)

    # -- esqueleto ---------------------------------------------------------------
    def _ensure_group(self, root: zarr.Group, cube: PassCube) -> zarr.Group:
        nlat, nlon = len(cube.lats), len(cube.lons)
        if cube.res in root:
            grp = root[cube.res]
            if grp["latitude"].shape != (nlat,) or grp["longitude"].shape != (nlon,):  # pyright: ignore[reportAttributeAccessIssue]
                raise ValueError(f"malla distinta a la existente en {cube.res}")
            return grp  # pyright: ignore[reportReturnType]
        grp = root.create_group(cube.res)
        n_init = _n_init()
        nstep = len(STEPS_H)
        coords = {
            "init_time": (
                np.arange(n_init, dtype=np.int64) * INIT_FREQ_H,
                ("init_time",),
                {"units": _ATTR_UNITS_INIT, "calendar": "proleptic_gregorian"},
            ),
            "step": (np.asarray(STEPS_H, dtype=np.int64), ("step",), {"units": "hours"}),
            "level": (np.asarray(LEVELS, dtype=np.int64), ("level",), {"units": "hPa"}),
            "latitude": (cube.lats, ("latitude",), {"units": "degrees_north"}),
            "longitude": (cube.lons, ("longitude",), {"units": "degrees_east"}),
        }
        for name, (vals, dims, attrs) in coords.items():
            a = grp.create_array(name, shape=vals.shape, dtype=vals.dtype, dimension_names=dims)
            a[:] = vals
            a.attrs.update(attrs)
        for v in SURFACE_VARS:
            grp.create_array(
                v,
                shape=(n_init, nstep, nlat, nlon),
                chunks=(1, nstep, nlat, nlon),
                dtype="float32",
                fill_value=np.nan,
                dimension_names=("init_time", "step", "latitude", "longitude"),
            )
        for v in PRESSURE_VARS:
            grp.create_array(
                v,
                shape=(n_init, nstep, len(LEVELS), nlat, nlon),
                chunks=(1, nstep, len(LEVELS), nlat, nlon),
                dtype="float32",
                fill_value=np.nan,
                dimension_names=("init_time", "step", "level", "latitude", "longitude"),
            )
        grp.create_array(
            "lsm",
            shape=(nlat, nlon),
            dtype="float32",
            fill_value=np.nan,
            dimension_names=("latitude", "longitude"),
        )
        grp.create_array(
            "ingested", shape=(n_init,), dtype="int8", fill_value=0, dimension_names=("init_time",)
        )
        return grp

    # -- API ---------------------------------------------------------------------
    def _root_ro(self) -> zarr.Group | None:
        try:
            return zarr.open_group(self.repo.readonly_session(branch=BRANCH).store, mode="r")
        except GroupNotFoundError:
            return None

    def has_lsm(self, res: str) -> bool:
        root = self._root_ro()
        if root is None or res not in root:
            return False
        lsm = np.asarray(root[res]["lsm"][:])  # pyright: ignore[reportIndexIssue]
        return bool(np.isfinite(lsm).any())

    def write_pass(self, cube: PassCube, message: str) -> str | None:
        """Sobrescribe la region de la pasada y hace un commit. Devuelve el id del commit."""
        session = self.repo.writable_session(BRANCH)
        root = zarr.open_group(session.store, mode="a")
        grp = self._ensure_group(root, cube)
        i = init_index(cube.init)
        sidx = [STEPS_H.index(s) for s in cube.steps]
        nlat, nlon = len(cube.lats), len(cube.lons)
        for v in SURFACE_VARS:
            full = np.full((len(STEPS_H), nlat, nlon), np.nan, dtype=np.float32)
            if v in cube.data:
                full[sidx] = cube.data[v]
            grp[v][i] = full  # pyright: ignore[reportIndexIssue]
        for v in PRESSURE_VARS:
            full = np.full((len(STEPS_H), len(LEVELS), nlat, nlon), np.nan, dtype=np.float32)
            if v in cube.data:
                full[sidx] = cube.data[v]
            grp[v][i] = full  # pyright: ignore[reportIndexIssue]
        if cube.lsm is not None:
            grp["lsm"][:] = cube.lsm  # pyright: ignore[reportIndexIssue]
        grp["ingested"][i] = 1  # pyright: ignore[reportIndexIssue]
        try:
            return session.commit(message)
        except icechunk.IcechunkError as e:  # sin cambios
            if "no changes" in str(e).lower():
                return None
            raise

    def read_pass(self, res: str, init: dt.datetime) -> xr.Dataset:
        """Lee (en memoria) la region de una pasada."""
        store = self.repo.readonly_session(branch=BRANCH).store
        ds = xr.open_zarr(store, group=res, consolidated=False, chunks=None)
        i = init_index(init)
        return ds.isel(init_time=slice(i, i + 1)).load()

    def ingested_inits(self, res: str) -> list[dt.datetime]:
        root = self._root_ro()
        if root is None or res not in root:
            return []
        flags = np.asarray(root[res]["ingested"][:])  # pyright: ignore[reportIndexIssue]
        return [T0 + dt.timedelta(hours=int(k) * INIT_FREQ_H) for k in np.flatnonzero(flags)]
