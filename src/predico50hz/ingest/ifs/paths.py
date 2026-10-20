"""Resolucion de rutas del bucket `s3://ecmwf-forecasts` por fecha, pasada y paso.

Reglas medidas en la Etapa 0 (docs/stage0-index-check.md):

* hasta 2024-01-31 solo existe `{fecha}/{HH}z/0p4-beta/{stream}/` (sin prefijo `ifs/`);
* desde 2024-02-01 aparece `0p25` (y `0p4-beta` sigue publicandose);
* el prefijo `ifs/` aparece entre 2024-02-01 y 2024-03-01 (fecha exacta sin acotar);
* las pasadas 00Z/12Z van en `oper`; las 06Z/18Z van en `scda` en 2023-2024 y en
  `oper` en 2026 (fecha del cambio sin acotar).

Como hay fechas sin acotar, `candidate_dirs` devuelve las rutas posibles en orden de
preferencia y `resolve_layout` se queda con la primera cuyo `.index` existe.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

BASE_URL = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/"
RES_CHANGE = dt.date(2024, 2, 1)
# Heuristica solo para ordenar sondeos de 06Z/18Z; ambos streams se prueban siempre.
SCDA_LAST_YEAR = 2025


@dataclass(frozen=True)
class Layout:
    """Directorio resuelto para una (fecha, pasada)."""

    prefix: str  # p. ej. `20260415/00z/ifs/0p25/oper/`
    res: str  # `0p25` o `0p4-beta`
    stream: str  # `oper` o `scda`


def candidate_dirs(day: dt.date, cycle: int) -> list[Layout]:
    """Rutas posibles ordenadas por preferencia (0p25 antes que 0p4-beta)."""
    root = f"{day:%Y%m%d}/{cycle:02d}z/"
    if cycle in (0, 12):
        streams = ["oper"]
    elif day.year <= SCDA_LAST_YEAR:
        streams = ["scda", "oper"]
    else:
        streams = ["oper", "scda"]
    if day < RES_CHANGE:
        shapes = [("0p4-beta", "")]
    else:
        shapes = [
            ("0p25", "ifs/"),
            ("0p25", ""),
            ("0p4-beta", "ifs/"),
            ("0p4-beta", ""),
        ]
    return [
        Layout(f"{root}{ifs}{res}/{stream}/", res, stream)
        for res, ifs in shapes
        for stream in streams
    ]


def file_stem(day: dt.date, cycle: int, step: int, stream: str) -> str:
    return f"{day:%Y%m%d}{cycle:02d}0000-{step}h-{stream}-fc"


def index_key(layout: Layout, day: dt.date, cycle: int, step: int) -> str:
    return f"{layout.prefix}{file_stem(day, cycle, step, layout.stream)}.index"


def grib_key(layout: Layout, day: dt.date, cycle: int, step: int) -> str:
    return f"{layout.prefix}{file_stem(day, cycle, step, layout.stream)}.grib2"


def resolve_layout(
    day: dt.date, cycle: int, step: int, exists: Callable[[str], bool]
) -> Layout | None:
    """Primer candidato cuyo `.index` del paso dado existe, o `None`."""
    for cand in candidate_dirs(day, cycle):
        if exists(index_key(cand, day, cycle, step)):
            return cand
    return None
