"""Constantes de la ingesta desde dynamical.org (IFS ENS 0,25°)."""

import datetime as dt
from dataclasses import dataclass

DATASET_ID = "ecmwf-ifs-ens-forecast-15-day-0-25-degree"
STAC_URL = "https://stac.dynamical.org/catalog.json"
STAC_ASSET = "icechunk-https"
PINNED_VERSION = "0.1.0"

# PLAN 2.2: viento y termodinamica; la velocidad se deriva por miembro en plata.
VARIABLES: tuple[str, ...] = (
    "wind_u_100m",
    "wind_v_100m",
    "wind_u_10m",
    "wind_v_10m",
    "wind_gust_10m",
    "temperature_2m",
    "pressure_surface",
    "temperature_925hpa",
    "temperature_850hpa",
    "pressure_reduced_to_mean_sea_level",
)
LEAD_HOURS: tuple[int, ...] = tuple(range(18, 55, 3))
N_MEMBERS = 51
CONTROL_MEMBER = 0

# Pares (u, v) -> nombre de la velocidad derivada.
SPEEDS: dict[str, tuple[str, str]] = {
    "wind_speed_100m": ("wind_u_100m", "wind_v_100m"),
    "wind_speed_10m": ("wind_u_10m", "wind_v_10m"),
}

# Eje init_time prerreservado en bronce: una pasada = una region fija (escritura idempotente).
INIT_START = dt.datetime(2024, 4, 1)
INIT_END = dt.datetime(2028, 1, 1)  # exclusivo

DEFAULT_BRONZE = "/mnt/data/nwp/IFS-ENS/icechunk"
DEFAULT_SILVER = "data/silver/ifs_ens_clusters"
DEFAULT_CENTROIDS = "config/centroids.yaml"
DEFAULT_METRICS = "data/stage1/dynamical_metrics.json"


@dataclass(frozen=True)
class Domain:
    lat_min: float = 49.5
    lat_max: float = 56.0
    lon_min: float = 8.5
    lon_max: float = 16.0


DOMAIN = Domain()
