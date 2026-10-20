"""Campos de IFS que se extraen (PLAN 2.2) y su nombre de variable en bronce."""

from dataclasses import dataclass

LEVELS: tuple[int, ...] = (1000, 925, 850)
PL_PARAMS: tuple[str, ...] = ("u", "v", "t")


@dataclass(frozen=True)
class Field:
    """Un campo GRIB: parametro, tipo de nivel y nivel (solo `pl`)."""

    param: str
    levtype: str
    levelist: int | None
    var: str  # nombre de variable en bronce (sin nivel)

    @property
    def label(self) -> str:
        return self.param if self.levelist is None else f"{self.param}@{self.levelist}"


SURFACE_FIELDS: tuple[Field, ...] = (
    Field("100u", "sfc", None, "u100"),
    Field("100v", "sfc", None, "v100"),
    Field("10u", "sfc", None, "u10"),
    Field("10v", "sfc", None, "v10"),
    Field("10fg", "sfc", None, "fg10"),
    Field("2t", "sfc", None, "t2m"),
    Field("sp", "sfc", None, "sp"),
)
PRESSURE_FIELDS: tuple[Field, ...] = tuple(
    Field(p, "pl", lv, p) for p in PL_PARAMS for lv in LEVELS
)
LSM = Field("lsm", "sfc", None, "lsm")

DYNAMIC_FIELDS: tuple[Field, ...] = SURFACE_FIELDS + PRESSURE_FIELDS
ALL_FIELDS: tuple[Field, ...] = (*DYNAMIC_FIELDS, LSM)

# Variables de bronce con dimensiones (step, lat, lon) y (step, level, lat, lon).
SURFACE_VARS: tuple[str, ...] = tuple(f.var for f in SURFACE_FIELDS)
PRESSURE_VARS: tuple[str, ...] = PL_PARAMS
