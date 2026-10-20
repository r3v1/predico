# pyright: basic, reportArgumentType=false, reportCallIssue=false, reportIndexIssue=false
"""Capa bronce: recorte original (float32, 51 miembros) en nuestro Icechunk.

Un unico grupo raiz con un array por variable, dims (init_time, lead_time, ensemble_member,
latitude, longitude). El eje `init_time` esta prerreservado (diario, 2024-04-01..2027-12-31)
y los chunks son (1, lead, miembro, lat, lon): escribir una pasada es sobrescribir una region
fija, idempotente y valido en cualquier orden (el relleno historico puede ir hacia atras).
"""

import datetime as dt
import hashlib
from pathlib import Path

import icechunk
import numpy as np
import xarray as xr
import zarr
from zarr.errors import GroupNotFoundError

from predico50hz.ingest.dynamical.config import (
    INIT_END,
    INIT_START,
    LEAD_HOURS,
    N_MEMBERS,
    VARIABLES,
)
from predico50hz.ingest.dynamical.source import Cube, Source

BRANCH = "main"
DIMS = ("init_time", "lead_time", "ensemble_member", "latitude", "longitude")
_INIT_UNITS = f"days since {INIT_START:%Y-%m-%d} 00:00:00"


def n_init() -> int:
    return (INIT_END - INIT_START).days


def init_pos(init: dt.date) -> int:
    d = (init - INIT_START.date()).days
    if d < 0 or d >= n_init():
        raise ValueError(f"init_time fuera del eje bronce: {init}")
    return d


class BronzeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.repo = icechunk.Repository.open_or_create(
            icechunk.local_filesystem_storage(str(self.path))
        )

    # -- esqueleto ---------------------------------------------------------------
    def _ensure(
        self, root: zarr.Group, cube: Cube, src: Source, variables: tuple[str, ...]
    ) -> None:
        nlat, nlon = len(cube.lats), len(cube.lons)
        if "latitude" in root:
            if root["latitude"].shape != (nlat,) or root["longitude"].shape != (nlon,):  # pyright: ignore[reportAttributeAccessIssue]
                raise ValueError("malla distinta a la existente en bronce")
            return
        coords = {
            "init_time": (np.arange(n_init(), dtype=np.int64), ("init_time",), _INIT_UNITS),
            "lead_time": (np.asarray(LEAD_HOURS, dtype=np.int64), ("lead_time",), "hours"),
            "ensemble_member": (np.arange(N_MEMBERS, dtype=np.int16), ("ensemble_member",), "1"),
            "latitude": (cube.lats, ("latitude",), "degrees_north"),
            "longitude": (cube.lons, ("longitude",), "degrees_east"),
        }
        for name, (vals, dims, units) in coords.items():
            a = root.create_array(name, shape=vals.shape, dtype=vals.dtype, dimension_names=dims)
            a[:] = vals
            a.attrs["units"] = units
        root["init_time"].attrs["calendar"] = "proleptic_gregorian"
        for v in variables:
            a = root.create_array(
                v,
                shape=(n_init(), len(LEAD_HOURS), N_MEMBERS, nlat, nlon),
                chunks=(1, len(LEAD_HOURS), N_MEMBERS, nlat, nlon),
                dtype="float32",
                fill_value=np.nan,
                dimension_names=DIMS,
            )
            src_attrs = src.ds[v].attrs
            a.attrs.update({k: str(src_attrs[k]) for k in ("long_name", "units") if k in src_attrs})
        root.create_array(
            "ingested",
            shape=(n_init(),),
            dtype="int8",
            fill_value=0,
            dimension_names=("init_time",),
        )
        root.attrs.update(
            {
                "source_href": src.href,
                "source_dataset_version": src.dataset_version,
                "lat_order": "ascending (invertida respecto al origen)",
                "provenance": {},
            }
        )

    # -- API ---------------------------------------------------------------------
    def write_pass(
        self, cube: Cube, init: dt.date, src: Source, message: str, variables: tuple[str, ...]
    ) -> str | None:
        """Sobrescribe la region de la pasada con un commit (devuelve su id, None si no cambia)."""
        session = self.repo.writable_session(BRANCH)
        root = zarr.open_group(session.store, mode="a")
        self._ensure(root, cube, src, variables)
        i = init_pos(init)
        for v in variables:
            root[v][i] = cube.data[v]
        root["ingested"][i] = 1
        prov = dict(root.attrs.get("provenance", {}))
        prov[f"{init:%Y-%m-%d}"] = {
            "source_href": src.href,
            "source_dataset_version": src.dataset_version,
            "source_snapshot_id": src.snapshot_id,
        }
        root.attrs["provenance"] = prov
        root.attrs["source_href"] = src.href
        root.attrs["source_dataset_version"] = src.dataset_version
        try:
            return session.commit(message)
        except icechunk.IcechunkError as e:
            if "no changes" in str(e).lower():
                return None
            raise

    def _root_ro(self) -> zarr.Group | None:
        try:
            return zarr.open_group(self.repo.readonly_session(branch=BRANCH).store, mode="r")
        except GroupNotFoundError:
            return None

    def is_ingested(self, init: dt.date) -> bool:
        root = self._root_ro()
        return root is not None and bool(root["ingested"][init_pos(init)])

    def read_pass(self, init: dt.date) -> xr.Dataset:
        store = self.repo.readonly_session(branch=BRANCH).store
        ds = xr.open_zarr(store, consolidated=False, chunks=None)
        i = init_pos(init)
        return ds.isel(init_time=slice(i, i + 1)).load()

    def digest(self, init: dt.date, variables: tuple[str, ...] = VARIABLES) -> str:
        """SHA-256 de los valores float32 de la pasada (todas las variables, en orden)."""
        root = self._root_ro()
        if root is None:
            raise FileNotFoundError(self.path)
        i = init_pos(init)
        h = hashlib.sha256()
        for v in variables:
            h.update(v.encode())
            h.update(np.ascontiguousarray(root[v][i], dtype=np.float32).tobytes())
        return h.hexdigest()

    def disk_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())
