# ruff: noqa: RUF002
"""Variables NWP derivadas a 15 min (F3.2), calculadas DESPUÉS de interpolar los estadísticos.

Entrada: `{var_stat: (T, P)}` de `nwp.interpolate_pass`, el tipo de cada clúster y los pesos de
capacidad (G × P, grupos `land`, `sea`, `all`). Salida: `{columna: (T,)}` en float64.
Disponibilidad: `available_at_nwp` (los pesos de capacidad de D son anteriores).
"""

import numpy as np
import numpy.typing as npt

from predico50hz.features.capacity import Cluster
from predico50hz.features.config import (
    GROUPS,
    HUB_HEIGHT_M,
    R_DRY_AIR,
    SHEAR_DEFAULT,
    SHEAR_MIN_SPEED,
    SHEAR_RANGE,
)
from predico50hz.features.interpolate import Array

CLUSTER_STATS = ("ctrl", "mean", "p10", "p50", "p90", "std")
MIN_SPEED_DENOM = 0.1  # m/s; denominador mínimo de cocientes (factor de racha, cv)


def ev3(mean: Array, std: Array) -> Array:
    """E[v³] = μ³ + 3 μ σ² suponiendo un ensemble gaussiano de media μ y desviación σ (m³/s³)."""
    return mean**3 + 3.0 * mean * std**2


def shear_exponent(ws100: Array, ws10: Array) -> Array:
    """α = ln(v100 / v10) / ln(10), recortado a [0, 0,5]; 1/7 si alguna velocidad < 0,5 m/s."""
    low = (ws100 < SHEAR_MIN_SPEED) | (ws10 < SHEAR_MIN_SPEED)
    safe100 = np.where(low, 1.0, ws100)
    safe10 = np.where(low, 1.0, ws10)
    alpha = np.log(safe100 / safe10) / np.log(10.0)
    return np.where(low, SHEAR_DEFAULT, np.clip(alpha, *SHEAR_RANGE))


def air_density(pressure_pa: Array, temp_c: Array) -> Array:
    """ρ = p / (R_d · T) en kg/m³, con p en Pa y T en °C."""
    return pressure_pa / (R_DRY_AIR * (temp_c + 273.15))


def hub_factor(alpha: Array, kinds: np.ndarray) -> Array:
    """Factor (h / 100)^α de 100 m a la altura de buje de cada tipo de clúster."""
    h = np.array([HUB_HEIGHT_M[k] for k in kinds])
    return (h / 100.0) ** alpha


def weighted_std(x: Array, w: Array) -> Array:
    """Desviación típica ponderada de `x` (T, P) con pesos `w` (P,) que suman 1."""
    mu = x @ w
    return np.sqrt(np.maximum(((x - mu[:, None]) ** 2) @ w, 0.0))


def derive(  # noqa: PLR0915
    c: dict[str, Array], clusters: list[Cluster], weights: Array
) -> tuple[dict[str, Array], npt.NDArray[np.int8]]:
    """Todas las variables NWP en ancho. Devuelve `(columnas, gust_missing (T,) int8)`."""
    ids = [k.point_id for k in clusters]
    kinds = np.array([k.kind for k in clusters])
    out: dict[str, Array] = {}

    def agg(x: Array) -> Array:
        return x @ weights.T  # (T, G)

    def put(prefix: str, x: Array, suffix: str = "") -> None:
        a = agg(x)
        for j, g in enumerate(GROUPS):
            out[f"{prefix}_{g}{suffix}"] = a[:, j]

    # Por clúster: viento a 100 m con todos los estadísticos y viento de buje medio.
    for j, pid in enumerate(ids):
        for s in CLUSTER_STATS:
            out[f"ws100_{pid}_{s}"] = c[f"wind_speed_100m_{s}"][:, j]
    m100, s100 = c["wind_speed_100m_mean"], c["wind_speed_100m_std"]
    m10 = c["wind_speed_10m_mean"]
    alpha = shear_exponent(m100, m10)
    fac = hub_factor(alpha, kinds)
    mh, sh = m100 * fac, s100 * fac
    for j, pid in enumerate(ids):
        out[f"wshub_{pid}_mean"] = mh[:, j]

    # Viento a 100 m y v³ agregados.
    for s in ("mean", "ctrl", "p10", "p90", "std"):
        put("ws100", c[f"wind_speed_100m_{s}"], f"_{s}")
    put("ws10", m10, "_mean")
    put("v3", c["wind_speed_100m_ctrl"] ** 3, "_ctrl")
    put("v3", ev3(m100, s100), "_ens")
    put("v3", c["wind_speed_100m_p10"] ** 3, "_p10")
    put("v3", c["wind_speed_100m_p90"] ** 3, "_p90")

    # Altura de buje, densidad y proxy de potencia.
    put("shear", alpha)
    put("wshub", mh, "_mean")
    ev3h = ev3(mh, sh)
    put("v3hub", ev3h, "_ens")
    rho = air_density(c["pressure_surface_mean"], c["temperature_2m_mean"])
    put("rho", rho)
    put("pdens", rho * ev3h)
    put("stab", c["temperature_925hpa_mean"] - c["temperature_2m_mean"])
    put("t2m", c["temperature_2m_mean"])

    # Racha (NaN solo si la plata no la trae) y dirección.
    gust_mean, gust_p90 = c["wind_gust_10m_mean"], c["wind_gust_10m_p90"]
    put("gust", gust_mean, "_mean")
    put("gust", gust_p90, "_p90")
    ws10_g = agg(m10)
    gust_g = agg(gust_mean)
    for j, g in enumerate(GROUPS):
        out[f"gustfac_{g}"] = gust_g[:, j] / np.maximum(ws10_g[:, j], MIN_SPEED_DENOM)
    gust_missing = np.isnan(gust_mean).any(axis=1).astype(np.int8)
    u, v = agg(c["wind_u_100m_mean"]), agg(c["wind_v_100m_mean"])
    speed = np.hypot(u, v)
    ok = speed > 1e-9  # noqa: PLR2004
    safe = np.where(ok, speed, 1.0)
    for j, g in enumerate(GROUPS):
        out[f"u100_{g}"] = u[:, j]
        out[f"v100_{g}"] = v[:, j]
        # Dirección meteorológica (de dónde sopla): sin = -u/|V|, cos = -v/|V|.
        out[f"wdir100_{g}_sin"] = np.where(ok[:, j], -u[:, j] / safe[:, j], 0.0)
        out[f"wdir100_{g}_cos"] = np.where(ok[:, j], -v[:, j] / safe[:, j], 0.0)

    # Dispersión espacial entre los clústeres terrestres.
    land = kinds == "land"
    wl = weights[0]
    x = m100[:, land]
    mean_l = m100 @ wl
    disp = weighted_std(m100, wl)
    out["disp_land_std"] = disp
    out["disp_land_range"] = x.max(axis=1) - x.min(axis=1)
    out["disp_land_cv"] = disp / np.maximum(mean_l, MIN_SPEED_DENOM)
    out["ws100_sea_minus_land"] = out["ws100_sea_mean"] - out["ws100_land_mean"]
    return out, gust_missing
