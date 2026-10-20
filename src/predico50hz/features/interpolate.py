"""Interpolación temporal de la malla NWP de 3 h a la malla de 15 min (F3.4).

Trabaja sobre arrays de NumPy con el tiempo en el primer eje. Los nodos (`x`) y las consultas
(`q`) están en segundos desde un origen común (la pasada NWP). Nunca extrapola.
"""

from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.interpolate import (  # pyright: ignore[reportMissingTypeStubs]
    CubicSpline,
    PchipInterpolator,
)

Array = npt.NDArray[np.float64]
Method = Literal["linear", "pchip", "cubic"]
MIN_NODES = 2


def _check_range(x: Array, q: Array) -> None:
    if x.ndim != 1 or x.size < MIN_NODES or np.any(np.diff(x) <= 0):
        raise ValueError("los nodos deben ser ≥ 2 y estrictamente crecientes")
    if q.size and (q.min() < x[0] or q.max() > x[-1]):
        raise ValueError("consulta fuera del rango de nodos (no se extrapola)")


def linear(x: Array, y: Array, q: Array) -> Array:
    """Interpolación lineal de `y` (n, ...) en `q` (m,) → (m, ...). Combinación convexa de nodos."""
    _check_range(x, q)
    i = np.clip(np.searchsorted(x, q, side="right") - 1, 0, x.size - 2)
    f = (q - x[i]) / (x[i + 1] - x[i])
    f = f.reshape((-1,) + (1,) * (y.ndim - 1))
    return y[i] * (1.0 - f) + y[i + 1] * f


def interpolate(method: Method, x: Array, y: Array, q: Array) -> Array:
    """Interpola `y` (n, ...) con `method` ("linear", "pchip" o spline cúbico) en `q` (m,)."""
    if method == "linear":
        return linear(x, y, q)
    _check_range(x, q)
    if method == "pchip":
        return np.asarray(PchipInterpolator(x, y, axis=0)(q), dtype=np.float64)
    return np.asarray(CubicSpline(x, y, axis=0)(q), dtype=np.float64)


def step_after(x: Array, y: Array, q_end: Array) -> Array:
    """Constante por tramos para máximos de ventana (racha).

    El valor del nodo `s` resume la ventana `(s - 3 h, s]`. El intervalo `[t, t + 15 min)` toma
    el primer nodo `s ≥ t + 15 min` (`q_end = t + 15 min`): es el que contiene el intervalo.
    """
    _check_range(x, q_end)
    return y[np.searchsorted(x, q_end, side="left")]
