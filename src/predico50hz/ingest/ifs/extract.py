# pyright: basic
"""Capa plata: interpolacion bilineal de los recortes en puntos (independiente de la malla)."""

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr
import yaml

from predico50hz.ingest.ifs.fields import LEVELS, PRESSURE_VARS, SURFACE_VARS


@dataclass(frozen=True)
class Point:
    id: str
    lat: float
    lon: float
    kind: str = ""


def load_points(path: str | Path) -> list[Point]:
    cfg = yaml.safe_load(Path(path).read_text())
    return [
        Point(p["id"], float(p["lat"]), float(p["lon"]), p.get("kind", "")) for p in cfg["points"]
    ]


def _axis_weights(axis: npt.NDArray[np.float64], x: float) -> tuple[int, float]:
    """Indice izquierdo y peso del vecino derecho en un eje ascendente."""
    if x < axis[0] or x > axis[-1]:
        raise ValueError(f"punto {x} fuera de la malla [{axis[0]}, {axis[-1]}]")
    i = int(np.clip(np.searchsorted(axis, x, side="right") - 1, 0, len(axis) - 2))
    return i, float((x - axis[i]) / (axis[i + 1] - axis[i]))


def bilinear(
    field: npt.NDArray[np.float32],
    lats: npt.NDArray[np.float64],
    lons: npt.NDArray[np.float64],
    lat: float,
    lon: float,
) -> npt.NDArray[np.float64]:
    """Interpola `field[..., lat, lon]` en (lat, lon); devuelve el array sin esas dos dims."""
    i, wy = _axis_weights(lats, lat)
    j, wx = _axis_weights(lons, lon)
    f = field.astype(np.float64)
    return (
        f[..., i, j] * (1 - wy) * (1 - wx)
        + f[..., i, j + 1] * (1 - wy) * wx
        + f[..., i + 1, j] * wy * (1 - wx)
        + f[..., i + 1, j + 1] * wy * wx
    )


def extract_points(ds: xr.Dataset, res: str, points: list[Point]) -> pa.Table:
    """Una fila por (init_time, step, punto); una columna por campo (nivel en el nombre)."""
    lats = np.asarray(ds["latitude"].values, dtype=np.float64)
    lons = np.asarray(ds["longitude"].values, dtype=np.float64)
    init = ds["init_time"].values[0].astype("datetime64[us]").astype(dt.datetime)
    steps = np.asarray(ds["step"].values, dtype=np.int64)
    keep = np.asarray(
        [
            k
            for k in range(len(steps))
            if any(np.isfinite(ds[v].values[0][k]).any() for v in (*SURFACE_VARS, *PRESSURE_VARS))
        ],
        dtype=np.intp,
    )
    cols: dict[str, list[npt.NDArray[np.float64]]] = {}
    ids: list[str] = []
    plat: list[float] = []
    plon: list[float] = []
    stepcol: list[npt.NDArray[np.int64]] = []
    for p in points:
        n = len(keep)
        ids += [p.id] * n
        plat += [p.lat] * n
        plon += [p.lon] * n
        stepcol.append(steps[keep])
        for v in SURFACE_VARS:
            cols.setdefault(v, []).append(bilinear(ds[v].values[0][keep], lats, lons, p.lat, p.lon))
        for v in PRESSURE_VARS:
            arr = bilinear(ds[v].values[0][keep], lats, lons, p.lat, p.lon)  # (step, level)
            for k, lv in enumerate(LEVELS):
                cols.setdefault(f"{v}{lv}", []).append(arr[:, k])
        cols.setdefault("lsm", []).append(
            np.full(n, float(bilinear(ds["lsm"].values, lats, lons, p.lat, p.lon)))
        )
    st = np.concatenate(stepcol)
    n_rows = len(st)
    data: dict[str, pa.Array] = {
        "init_time": pa.array([init] * n_rows, type=pa.timestamp("us")),
        "step_h": pa.array(st, type=pa.int32()),
        "valid_time": pa.array(
            [init + dt.timedelta(hours=int(s)) for s in st], type=pa.timestamp("us")
        ),
        "point_id": pa.array(ids),
        "lat": pa.array(plat, type=pa.float64()),
        "lon": pa.array(plon, type=pa.float64()),
        "source_res": pa.array([res] * n_rows),
    }
    for name, parts in cols.items():
        data[name] = pa.array(np.concatenate(parts).astype(np.float32), type=pa.float32())
    return pa.table(data)


def silver_path(root: str | Path, init: dt.datetime) -> Path:
    return Path(root) / f"init_date={init:%Y-%m-%d}" / f"cycle={init:%H}" / "part-0.parquet"


def write_silver(table: pa.Table, root: str | Path, init: dt.datetime) -> Path:
    """Escritura atomica y sobrescribible: relanzar una pasada reemplaza el mismo fichero."""
    out = silver_path(root, init)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(out)
    return out


def table_digest(table: pa.Table) -> str:
    """Hash del contenido (independiente de metadatos del fichero Parquet)."""
    h = hashlib.sha256()
    for name in table.column_names:
        h.update(name.encode())
        h.update(repr(table[name].to_pylist()).encode())
    return h.hexdigest()
