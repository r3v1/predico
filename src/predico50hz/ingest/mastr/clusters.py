# ruff: noqa: PLR0913, PLR0917
# pyright: basic
"""Clústeres de generación: k-means ponderado por capacidad (terrestre) y parques (marino).

Todo se calcula en un plano local en km (equirectangular centrado en `LAT0`): en 49-56° N el
error frente a la distancia geodésica es de unos pocos %, irrelevante para agrupar.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

LAT0 = 53.0
KM_PER_DEG = 111.195
F64 = npt.NDArray[np.float64]


def to_km(lat: F64, lon: F64) -> F64:
    """(n, 2) en km: x este, y norte, con el origen en (LAT0, 0)."""
    x = lon * KM_PER_DEG * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * KM_PER_DEG
    return np.column_stack([x, y])


def from_km(xy: F64) -> tuple[F64, F64]:
    lat = xy[:, 1] / KM_PER_DEG + LAT0
    lon = xy[:, 0] / (KM_PER_DEG * math.cos(math.radians(LAT0)))
    return lat, lon


@dataclass(frozen=True)
class KMeansResult:
    centers: F64  # (k, 2) km
    labels: npt.NDArray[np.intp]
    inertia: float  # suma ponderada de distancias² (km²·MW)
    rmse_km: float  # raíz del error cuadrático medio ponderado a su centroide


def _sqdist(xy: F64, c: F64) -> F64:
    d = xy[:, None, :] - c[None, :, :]
    return np.einsum("nkd,nkd->nk", d, d)


def _init_pp(xy: F64, w: F64, k: int, rng: np.random.Generator) -> F64:
    """k-means++ con probabilidad proporcional a peso x distancia² al centro más cercano."""
    centers = [xy[rng.choice(len(xy), p=w / w.sum())]]
    d2 = _sqdist(xy, np.array(centers))[:, 0]
    for _ in range(1, k):
        p = w * d2
        centers.append(xy[rng.choice(len(xy), p=p / p.sum())])
        d2 = np.minimum(d2, _sqdist(xy, centers[-1][None, :])[:, 0])
    return np.array(centers)


def _lloyd(xy: F64, w: F64, centers: F64, max_iter: int) -> tuple[F64, npt.NDArray[np.intp]]:
    labels = np.argmin(_sqdist(xy, centers), axis=1)
    for _ in range(max_iter):
        new = centers.copy()
        for j in range(len(centers)):
            m = labels == j
            if m.any():
                new[j] = np.average(xy[m], axis=0, weights=w[m])
        new_labels = np.argmin(_sqdist(xy, new), axis=1)
        done = np.array_equal(new_labels, labels)
        centers, labels = new, new_labels
        if done:
            break
    return centers, labels


def kmeans_weighted(
    xy: F64, w: F64, k: int, seed: int = 0, n_init: int = 10, max_iter: int = 300
) -> KMeansResult:
    """K-means de Lloyd ponderado con k-means++ y `n_init` reinicios (gana la menor inercia)."""
    if not 1 <= k <= len(xy):
        raise ValueError(f"k={k} fuera de [1, {len(xy)}]")
    if (w <= 0).any():
        raise ValueError("los pesos deben ser > 0")
    rng = np.random.default_rng(seed)
    best: KMeansResult | None = None
    for _ in range(n_init):
        centers, labels = _lloyd(xy, w, _init_pp(xy, w, k, rng), max_iter)
        d2 = _sqdist(xy, centers)[np.arange(len(xy)), labels]
        inertia = float((w * d2).sum())
        if best is None or inertia < best.inertia:
            best = KMeansResult(centers, labels, inertia, math.sqrt(inertia / float(w.sum())))
    assert best is not None
    return best


def assign_nearest(xy: F64, centers: F64) -> npt.NDArray[np.intp]:
    return np.argmin(_sqdist(xy, centers), axis=1)


def grid_distance_km(lat: F64, lon: F64, step: float = 0.25) -> F64:
    """Distancia (km) de cada punto al nodo más cercano de la malla de `step` grados."""
    nlat = np.round(lat / step) * step
    nlon = np.round(lon / step) * step
    d = to_km(lat, lon) - to_km(nlat, nlon)
    return np.hypot(d[:, 0], d[:, 1])


def agreement(a: npt.NDArray[np.intp], b: npt.NDArray[np.intp]) -> float:
    """Índice de Rand entre dos particiones (1 = idénticas), sin dependencias."""
    n = len(a)
    ca = np.unique(a, return_inverse=True)[1]
    cb = np.unique(b, return_inverse=True)[1]
    table = np.zeros((ca.max() + 1, cb.max() + 1))
    np.add.at(table, (ca, cb), 1)
    comb = lambda x: x * (x - 1) / 2  # noqa: E731
    same_both = comb(table).sum()
    same_a = comb(table.sum(axis=1)).sum()
    same_b = comb(table.sum(axis=0)).sum()
    total = comb(n)
    return float((total + 2 * same_both - same_a - same_b) / total)


def min_center_distance(centers: F64) -> float:
    """Distancia mínima (km) entre dos centroides distintos."""
    d = np.sqrt(_sqdist(centers, centers))
    np.fill_diagonal(d, np.inf)
    return float(d.min())


def group_by_proximity(xy: F64, threshold_km: float) -> npt.NDArray[np.intp]:
    """Etiquetas de enlace simple: puntos a menos de `threshold_km` (en cadena) van juntos."""
    n = len(xy)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    d2 = _sqdist(xy, xy)
    for i in range(n):
        for j in range(i + 1, n):
            if d2[i, j] < threshold_km**2:
                parent[find(j)] = find(i)
    roots = np.array([find(i) for i in range(n)])
    return np.unique(roots, return_inverse=True)[1].astype(np.intp)
