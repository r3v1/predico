"""Registro explícito de columnas de la tabla oro: cada una es `meta`, `feature` o `target`.

Cada `feature` pertenece a un bloque (`nwp`, `capacity`, `calendar`) y cada bloque tiene su
columna `available_at_<bloque>` (meta). Las variables agregadas por capacidad llevan el bloque
`nwp`: dependen también de los pesos de capacidad, pero su disponibilidad (D 00:00 UTC) es
anterior a la de la pasada NWP, así que `available_at_nwp` es el máximo de ambas. Nada de la
generación, del pronóstico del operador ni de `split_v01` es feature.
"""

from dataclasses import dataclass
from typing import Literal

from predico50hz.features.capacity import Cluster
from predico50hz.features.config import GROUPS

Role = Literal["meta", "feature", "target"]
BLOCKS = ("nwp", "capacity", "calendar")
CLUSTER_STATS = ("ctrl", "mean", "p10", "p50", "p90", "std")

KEY = ("forecast_issue_date", "ts_utc")
META = (
    "forecast_issue_date",
    "target_date",
    "ts_utc",
    "closure_utc",
    "nwp_init_time",
    "available_at_nwp",
    "available_at_capacity",
    "available_at_calendar",
    "n_steps_day",
    "capacity_ffilled",
    "split_v01",
)
TARGETS = (
    "y_gen_mw",
    "y_gen_land_mw",
    "y_gen_sea_mw",
    "y_cap_mw",
    "y_cap_land_mw",
    "y_cap_sea_mw",
    "y_cf",
    "y_cf_land",
    "y_cf_sea",
    "y_is_gap_filled",
)
# (prefijo, sufijo) de las familias agregadas por grupo: `<prefijo>_<grupo><sufijo>`.
GROUP_FAMILIES: tuple[tuple[str, str], ...] = (
    *(("ws100", f"_{s}") for s in ("mean", "ctrl", "p10", "p90", "std")),
    ("ws10", "_mean"),
    *(("v3", f"_{s}") for s in ("ctrl", "ens", "p10", "p90")),
    ("shear", ""),
    ("wshub", "_mean"),
    ("v3hub", "_ens"),
    ("rho", ""),
    ("pdens", ""),
    ("stab", ""),
    ("t2m", ""),
    ("gust", "_mean"),
    ("gust", "_p90"),
    ("gustfac", ""),
    ("u100", ""),
    ("v100", ""),
    ("wdir100", "_sin"),
    ("wdir100", "_cos"),
)
NWP_EXTRA = (
    "gust_missing",
    "disp_land_std",
    "disp_land_range",
    "disp_land_cv",
    "ws100_sea_minus_land",
    "lead_hours",
)
CAPACITY_FEATURES = ("cap_land_mw", "cap_sea_mw", "cap_all_mw", "cap_sea_share")
CALENDAR_FEATURES = (
    "hour_local",
    "hour_sin",
    "hour_cos",
    "doy_sin",
    "doy_cos",
    "dow",
    "month",
    "is_weekend",
    "is_dst",
    "step_of_day",
)
# Columnas que valen NaN cuando la plata NWP no trae racha (`gust_missing` = 1).
GUST_PREFIXES = ("gust_", "gustfac_")


@dataclass(frozen=True)
class Column:
    name: str
    role: Role
    block: str | None = None


def available_col(block: str) -> str:
    """Nombre de la columna de disponibilidad de un bloque."""
    return f"available_at_{block}"


def nwp_columns(clusters: list[Cluster]) -> list[str]:
    """Columnas del bloque `nwp`, en orden."""
    cols = [f"ws100_{c.point_id}_{s}" for c in clusters for s in CLUSTER_STATS]
    cols += [f"wshub_{c.point_id}_mean" for c in clusters]
    cols += [f"{p}_{g}{s}" for p, s in GROUP_FAMILIES for g in GROUPS]
    return [*cols, *NWP_EXTRA]


def build_registry(clusters: list[Cluster]) -> list[Column]:
    """Todas las columnas de la tabla oro con su rol y bloque, en el orden de la tabla."""
    cols = [Column(m, "meta") for m in META]
    cols += [Column(n, "feature", "nwp") for n in nwp_columns(clusters)]
    cols += [Column(n, "feature", "capacity") for n in CAPACITY_FEATURES]
    cols += [Column(n, "feature", "calendar") for n in CALENDAR_FEATURES]
    cols += [Column(t, "target") for t in TARGETS]
    names = [c.name for c in cols]
    if len(set(names)) != len(names):
        raise ValueError("columnas duplicadas en el registro")
    return cols


def feature_columns(reg: list[Column]) -> list[str]:
    return [c.name for c in reg if c.role == "feature"]


def is_gust(name: str) -> bool:
    """`True` si la columna vale NaN cuando falta la racha."""
    return name.startswith(GUST_PREFIXES) and name != "gust_missing"
