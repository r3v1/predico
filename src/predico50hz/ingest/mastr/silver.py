"""Capa plata de MaStR (Delta Lake en `data/silver/mastr/`), reconstruida entera en cada ejecución.

Tablas: `units` (unidades eólicas de Alemania, con `in_zone` y `cluster_id`), `clusters`
(centroides, capacidad actual), `capacity_cluster` (día x clúster) y `capacity_total` (día x
tierra/mar). La escritura es idempotente: no crea versión de Delta si el contenido es idéntico.
"""

from pathlib import Path

import polars as pl


def silver_path(root: Path, table: str) -> Path:
    return root / table


def write_silver(df: pl.DataFrame, path: Path) -> bool:
    """Escritura idempotente; devuelve `True` si creó una versión nueva de la tabla."""
    if (path / "_delta_log").exists():
        current = pl.read_delta(str(path))
        if current.shape == df.shape and current.equals(df.select(current.columns)):
            return False
    df.write_delta(str(path), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    return True


def read_silver(root: Path, table: str) -> pl.DataFrame:
    return pl.read_delta(str(silver_path(root, table)))
