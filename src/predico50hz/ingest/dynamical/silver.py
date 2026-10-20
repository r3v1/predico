# pyright: basic
"""Capa plata: bilineal en los centroides y agregados del ensemble por punto, init y paso.

Orden de las operaciones (importante, la velocidad no es lineal):
1. bilineal de cada miembro en el punto (u, v y resto de variables);
2. velocidad del viento POR MIEMBRO a partir de (u, v) interpolados;
3. agregados del ensemble (control, media, std, p10, p50, p90) de cada variable y velocidad.
"""

import hashlib
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from predico50hz.ingest.dynamical.config import CONTROL_MEMBER, SPEEDS, VARIABLES
from predico50hz.ingest.dynamical.source import Cube
from predico50hz.ingest.ifs.extract import Point, bilinear

STATS: tuple[str, ...] = ("ctrl", "mean", "std", "p10", "p50", "p90")
QUANTILES = (0.1, 0.5, 0.9)


def wind_speed(u: npt.NDArray[np.floating], v: npt.NDArray[np.floating]) -> npt.NDArray[np.float64]:
    return np.hypot(u.astype(np.float64), v.astype(np.float64))


def ensemble_stats(x: npt.NDArray[np.float64], axis: int = 0) -> dict[str, npt.NDArray[np.float64]]:
    """Control (miembro 0), media, std muestral (ddof=1) y p10/p50/p90 sobre los miembros."""
    q = np.quantile(x, QUANTILES, axis=axis)
    return {
        "ctrl": np.take(x, CONTROL_MEMBER, axis=axis),
        "mean": x.mean(axis=axis),
        "std": x.std(axis=axis, ddof=1),
        "p10": q[0],
        "p50": q[1],
        "p90": q[2],
    }


def point_members(cube: Cube, point: Point) -> dict[str, npt.NDArray[np.float64]]:
    """Por variable y velocidad: array (lead, member) en el punto, miembros sin agregar."""
    out = {
        v: bilinear(cube.data[v], cube.lats, cube.lons, point.lat, point.lon)
        for v in cube.data
        if v in VARIABLES
    }
    for name, (u, v) in SPEEDS.items():
        if u in out and v in out:
            out[name] = wind_speed(out[u], out[v])
    return out


def build_table(
    cube: Cube, points: list[Point], init: np.datetime64, run_info: dict[str, str]
) -> pa.Table:
    """Una fila por (punto, paso); columnas `<variable>_<stat>`, ordenadas de forma estable."""
    init_us = init.astype("datetime64[us]")
    n_lead = len(cube.lead_hours)
    leads = np.asarray(cube.lead_hours, dtype=np.int32)
    cols: dict[str, list[npt.NDArray[np.float32]]] = {}
    ids: list[str] = []
    plat: list[float] = []
    plon: list[float] = []
    lead_col: list[npt.NDArray[np.int32]] = []
    for p in points:
        ids += [p.id] * n_lead
        plat += [p.lat] * n_lead
        plon += [p.lon] * n_lead
        lead_col.append(leads)
        for name, arr in point_members(cube, p).items():  # arr: (lead, member)
            for stat, val in ensemble_stats(arr, axis=1).items():
                cols.setdefault(f"{name}_{stat}", []).append(val.astype(np.float32))
    lead_all = np.concatenate(lead_col)
    valid = init_us + lead_all.astype("timedelta64[h]")
    data: dict[str, pa.Array] = {
        "init_time": pa.array(np.full(len(lead_all), init_us), type=pa.timestamp("us")),
        "lead_hours": pa.array(lead_all, type=pa.int32()),
        "valid_time": pa.array(valid.astype("datetime64[us]"), type=pa.timestamp("us")),
        "point_id": pa.array(ids),
        "lat": pa.array(plat, type=pa.float64()),
        "lon": pa.array(plon, type=pa.float64()),
    }
    for k in sorted(cols):
        data[k] = pa.array(np.concatenate(cols[k]), type=pa.float32())
    return pa.table(data).replace_schema_metadata(run_info)


def silver_path(root: str | Path, init_date: str) -> Path:
    return Path(root) / f"init_date={init_date}" / "part-0.parquet"


def write_silver(table: pa.Table, root: str | Path, init_date: str) -> Path:
    """Escritura atomica y sobrescribible: relanzar una pasada reemplaza el mismo fichero."""
    out = silver_path(root, init_date)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(out)
    return out


def table_digest(table: pa.Table) -> str:
    """Hash del contenido (columnas y valores; sin metadatos de la tabla)."""
    h = hashlib.sha256()
    for name in table.column_names:
        h.update(name.encode())
        col = table[name].combine_chunks()
        if pa.types.is_string(col.type):
            h.update("\x00".join(col.to_pylist()).encode())
        else:
            h.update(col.to_numpy(zero_copy_only=False).tobytes())
    return h.hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
