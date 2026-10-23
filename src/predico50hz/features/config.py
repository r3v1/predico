"""Constantes y rutas de la capa de variables (F3). Todo el tiempo es UTC salvo que se indique."""

import datetime as dt
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
UTC = dt.UTC

# Rutas por defecto (relativas a la raíz del repositorio).
DEFAULT_NWP = "data/silver/ifs_ens_clusters"
DEFAULT_GENERATION = "data/silver/entsoe/generation"
DEFAULT_CAP_CLUSTER = "data/silver/mastr/capacity_cluster"
DEFAULT_CAP_TOTAL = "data/silver/mastr/capacity_total"
DEFAULT_CENTROIDS = "config/centroids.yaml"
DEFAULT_PRICES = "data/silver/entsoe/prices"
DEFAULT_GOLD = "data/gold/features"
DEFAULT_VALIDATION = "data/validation"

# Cierre de Predico: 15:00 hora local de Europe/Berlin del día D.
CLOSURE_LOCAL = dt.time(15, 0)
STEP = dt.timedelta(minutes=15)
NWP_STEP = dt.timedelta(hours=3)

# Pasada de dynamical usada para D: la 00Z de D. Publicación medida en el peor caso: 08:23 UTC
# (docs/stage0-dynamical.md §3); se añade margen hasta las 08:30.
NWP_INIT_HOUR = 0
NWP_LATENCY = dt.timedelta(hours=8, minutes=30)
# La capacidad y el calendario de D se consideran conocidos desde las 00:00 UTC de D.
CAPACITY_LATENCY = dt.timedelta(0)
CALENDAR_LATENCY = dt.timedelta(0)
# El precio de D se publica la víspera. Cota prudente de su disponibilidad: el cierre de D-1 más el
# peor retraso medido (6 h; `docs/f2-entsoe.md` §3, ADR 0031). Es siempre anterior al cierre de D.
PRICE_BOUND = dt.timedelta(hours=6)

# Altura de buje (m) ponderada por potencia, MaStR (docs/f2.6-mastr.md §8).
HUB_HEIGHT_M = {"land": 119.0, "sea": 104.0}
SHEAR_DEFAULT = 1.0 / 7.0
SHEAR_RANGE = (0.0, 0.5)
SHEAR_MIN_SPEED = 0.5  # m/s; por debajo de esta velocidad (10 o 100 m) se usa SHEAR_DEFAULT
R_DRY_AIR = 287.05  # J/(kg·K)

GROUPS = ("land", "sea", "all")
STATS = ("ctrl", "mean", "std", "p10", "p50", "p90")
# Método de las variables instantáneas: "linear" o "pchip" (docs/f3-features.md §3).
INSTANT_INTERP = "linear"

# Partición de la v0.1 por `target_date` (ADR 0010): (nombre, primer día, último día).
# Con el histórico completo (abr 2024 a jul 2026): `train` son los 6 meses que solo entrenan (el
# mínimo del primer pliegue) y `valid` los meses que se validan uno a uno, que además entrenan
# los pliegues posteriores. `train` termina `DEFAULT_MIN_TRAIN_MONTHS` meses después del primero.
SPLITS: tuple[tuple[str, dt.date, dt.date], ...] = (
    ("train", dt.date(2024, 4, 1), dt.date(2024, 9, 30)),
    ("valid", dt.date(2024, 10, 1), dt.date(2026, 7, 31)),
    ("holdout", dt.date(2026, 8, 1), dt.date(2100, 1, 1)),
)
SPLIT_UNUSED = "unused"
