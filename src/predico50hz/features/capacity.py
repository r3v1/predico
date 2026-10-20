# ruff: noqa: RUF002
"""Capacidad instalada de MaStR por clúster y fecha: pesos del agregado y variables de capacidad.

Disponibilidad: la capacidad de la fecha D se considera conocida desde D 00:00 UTC. Más allá
del último día de MaStR se arrastra el último valor y se avisa con `ffilled`.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl
import yaml

from predico50hz.features.config import GROUPS

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Cluster:
    """Clúster de MaStR / punto NWP: `point_id` y tipo (`land` o `sea`)."""

    point_id: str
    kind: str


def load_clusters(centroids: str | Path) -> list[Cluster]:
    """Clústeres de `config/centroids.yaml` en el orden del fichero."""
    cfg = yaml.safe_load(Path(centroids).read_text())
    return [Cluster(str(p["id"]), str(p["kind"])) for p in cfg["points"]]


def read_cluster_capacity(path: str | Path) -> pl.DataFrame:
    """Tabla diaria (`date`, `cluster_id`, `capacity_mw`) de MaStR."""
    return pl.read_delta(str(path)).select("date", "cluster_id", "capacity_mw")


@dataclass(frozen=True)
class DayCapacity:
    """Capacidad del día D: MW por clúster, pesos por grupo (G × P) y si se arrastró."""

    mw: Array
    weights: Array
    group_mw: Array
    ffilled: bool


def day_capacity(cap: pl.DataFrame, clusters: list[Cluster], d: dt.date) -> DayCapacity:
    """Pesos `cap_c / Σ cap` de cada grupo (`land`, `sea`, `all`) con la capacidad de la fecha `d`.

    Si `d` supera la última fecha de la tabla se usa la última (`ffilled=True`).
    """
    last = cap.get_column("date").max()
    if not isinstance(last, dt.date):
        raise ValueError("tabla de capacidad vacía")
    use = min(d, last)
    row = dict(
        cap.filter(pl.col("date") == use).select("cluster_id", "capacity_mw").iter_rows()  # pyright: ignore[reportArgumentType]
    )
    missing = [c.point_id for c in clusters if c.point_id not in row]
    if missing:
        raise ValueError(f"sin capacidad MaStR para {missing} el {use}")
    mw = np.array([row[c.point_id] for c in clusters], dtype=np.float64)
    kinds = np.array([c.kind for c in clusters])
    masks = [(kinds == g) if g != "all" else np.ones(len(clusters), bool) for g in GROUPS]
    group_mw = np.array([mw[m].sum() for m in masks])
    if np.any(group_mw <= 0):
        raise ValueError(f"capacidad total nula en un grupo el {use}")
    weights = np.stack([np.where(m, mw, 0.0) / mw[m].sum() for m in masks])
    return DayCapacity(mw, weights, group_mw, d > last)


def capacity_features(cap: DayCapacity) -> dict[str, float]:
    """Capacidad de la fecha D (MW) y cuota marina; bloque `capacity`."""
    out = {f"cap_{g}_mw": float(v) for g, v in zip(GROUPS, cap.group_mw, strict=True)}
    out["cap_sea_share"] = float(cap.group_mw[1] / cap.group_mw[2])
    return out
