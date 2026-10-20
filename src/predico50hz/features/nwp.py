"""Lectura de la plata NWP (IFS ENS por clúster) e interpolación a la malla de 15 min.

Disponibilidad: la pasada 00Z de D está en dynamical desde ~08:23 UTC; se usa
`available_at_nwp = D 00:00 + 8 h 30 min`. Solo se lee la pasada de D, nunca otra.
"""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl

from predico50hz.features import interpolate as itp
from predico50hz.features.config import INSTANT_INTERP, STATS
from predico50hz.features.interpolate import Array, Method
from predico50hz.features.timing import nwp_init_time

INSTANT_VARS = (
    "wind_speed_100m",
    "wind_speed_10m",
    "wind_u_100m",
    "wind_v_100m",
    "wind_u_10m",
    "wind_v_10m",
    "temperature_2m",
    "temperature_925hpa",
    "temperature_850hpa",
    "pressure_surface",
    "pressure_reduced_to_mean_sea_level",
)
GUST_VAR = "wind_gust_10m"
LEADS = tuple(range(18, 55, 3))
SPEED_VARS = ("wind_speed_100m", "wind_speed_10m")


def pass_path(root: Path, d: dt.date) -> Path:
    return root / f"init_date={d.isoformat()}" / "part-0.parquet"


def list_init_dates(root: Path) -> list[dt.date]:
    """Fechas de init de las pasadas presentes en la plata, ordenadas."""
    return sorted(
        dt.date.fromisoformat(p.name.removeprefix("init_date="))
        for p in root.glob("init_date=*")
        if (p / "part-0.parquet").exists()
    )


def read_pass(root: Path, d: dt.date, point_ids: list[str]) -> pl.DataFrame | None:
    """Pasada 00Z de `d` o `None` si falta. Valida init_time y nº de filas (`ValueError`)."""
    path = pass_path(root, d)
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    init = nwp_init_time(d).replace(tzinfo=None)
    if df.get_column("init_time").unique().to_list() != [init]:
        raise ValueError(f"{path}: init_time distinto de {init}")
    if df.height != len(point_ids) * len(LEADS):
        raise ValueError(f"{path}: {df.height} filas, se esperaban {len(point_ids) * len(LEADS)}")
    return df.sort("lead_hours", "point_id")


def _cube(df: pl.DataFrame, point_ids: list[str], col: str) -> Array:
    """(n_pasos, n_puntos) de una columna, con los puntos en el orden de `point_ids`."""
    wide = df.pivot(on="point_id", index="lead_hours", values=col).sort("lead_hours")
    leads = wide.get_column("lead_hours").to_list()
    if leads != list(LEADS):
        raise ValueError(f"pasos {leads} distintos de {list(LEADS)}")
    return wide.select(point_ids).to_numpy().astype(np.float64)


def _postprocess(cols: dict[str, Array]) -> None:
    """Restituye las cotas físicas tras interpolar: p10 ≤ p50 ≤ p90, std ≥ 0 y velocidades ≥ 0."""
    for v in INSTANT_VARS:
        q = np.sort(np.stack([cols[f"{v}_p10"], cols[f"{v}_p50"], cols[f"{v}_p90"]]), axis=0)
        cols[f"{v}_p10"], cols[f"{v}_p50"], cols[f"{v}_p90"] = q
        cols[f"{v}_std"] = np.maximum(cols[f"{v}_std"], 0.0)
    for v in SPEED_VARS:
        for s in STATS:
            cols[f"{v}_{s}"] = np.maximum(cols[f"{v}_{s}"], 0.0)


def interpolate_pass(
    df: pl.DataFrame,
    point_ids: list[str],
    grid: list[dt.datetime],
    d: dt.date,
    method: Method = INSTANT_INTERP,
) -> dict[str, Array]:
    """Estadísticos del ensemble por clúster en la malla de 15 min: `{var_stat: (T, P)}`.

    Instantáneas (`INSTANT_VARS`): interpolación en el punto medio de cada intervalo (la
    generación de ENTSO-E es la media del intervalo). Racha (`wind_gust_10m`, máximo de la
    ventana `(s - 3 h, s]`): constante por tramos con `itp.step_after`; NaN si la plata no la
    trae. Unidades: m/s, °C y Pa. Disponible desde `available_at_nwp(d)`.
    """
    init = nwp_init_time(d)
    x = np.array([h * 3600.0 for h in LEADS])
    start = np.array([(t - init).total_seconds() for t in grid])
    mid = start + 450.0
    end = start + 900.0
    cols: dict[str, Array] = {}
    names = [f"{v}_{s}" for v in INSTANT_VARS for s in STATS]
    cube = np.stack([_cube(df, point_ids, n) for n in names], axis=-1)  # (13, P, C)
    out = itp.interpolate(method, x, cube, mid)
    cols.update({n: out[:, :, k] for k, n in enumerate(names)})
    _postprocess(cols)
    for s in STATS:
        n = f"{GUST_VAR}_{s}"
        cols[n] = itp.step_after(x, _cube(df, point_ids, n), end)
    return cols
