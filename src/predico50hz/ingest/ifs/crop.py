# pyright: basic, reportArgumentType=false
"""Decodificacion de mensajes GRIB2 en memoria y recorte al dominio.

Nunca se escribe un GRIB global en disco: los bytes llegan de `download` y solo se
conserva el recorte (`float32`).
"""

from dataclasses import dataclass

import eccodes
import numpy as np
import numpy.typing as npt

# Dominio del ADR 0003 (grados).
LAT_MIN, LAT_MAX = 49.5, 56.0
LON_MIN, LON_MAX = 8.5, 16.0
EPS = 1e-6


@dataclass(frozen=True)
class Domain:
    lat_min: float = LAT_MIN
    lat_max: float = LAT_MAX
    lon_min: float = LON_MIN
    lon_max: float = LON_MAX


@dataclass(frozen=True)
class Cropped:
    """Recorte regular con latitudes y longitudes ascendentes."""

    values: npt.NDArray[np.float32]  # (lat, lon)
    lats: npt.NDArray[np.float64]
    lons: npt.NDArray[np.float64]
    short_name: str


def crop_axes(
    lats: npt.NDArray[np.float64], lons: npt.NDArray[np.float64], dom: Domain
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    """Indices (ordenados por coordenada ascendente) de las filas y columnas dentro del dominio."""
    ilat = np.flatnonzero((lats >= dom.lat_min - EPS) & (lats <= dom.lat_max + EPS))
    ilon = np.flatnonzero((lons >= dom.lon_min - EPS) & (lons <= dom.lon_max + EPS))
    return ilat[np.argsort(lats[ilat])], ilon[np.argsort(lons[ilon])]


def decode_and_crop(msg: bytes, dom: Domain | None = None) -> Cropped:
    """Decodifica un mensaje GRIB2 de malla regular lat/lon y lo recorta."""
    dom = dom or Domain()
    h = eccodes.codes_new_from_message(msg)
    try:
        grid = str(eccodes.codes_get(h, "gridType"))
        if grid != "regular_ll":
            raise ValueError(f"malla no soportada: {grid}")
        ni = int(eccodes.codes_get(h, "Ni"))
        nj = int(eccodes.codes_get(h, "Nj"))
        if int(eccodes.codes_get(h, "jPointsAreConsecutive")):
            raise ValueError("barrido por columnas no soportado")
        lats = np.asarray(eccodes.codes_get_double_array(h, "distinctLatitudes"), dtype=np.float64)
        lon0 = float(eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees"))
        dlon = float(eccodes.codes_get(h, "iDirectionIncrementInDegrees"))
        sign = -1.0 if int(eccodes.codes_get(h, "iScansNegatively")) else 1.0
        lons = (lon0 + sign * dlon * np.arange(ni) + 180.0) % 360.0 - 180.0
        values = np.asarray(eccodes.codes_get_values(h), dtype=np.float32).reshape(nj, ni)
        short = str(eccodes.codes_get(h, "shortName"))
    finally:
        eccodes.codes_release(h)
    if lats.shape[0] != nj:
        raise ValueError("numero de latitudes inconsistente")
    ilat, ilon = crop_axes(lats, lons, dom)
    sub = values[np.ix_(ilat, ilon)].copy()
    return Cropped(sub, lats[ilat], lons[ilon], short)
