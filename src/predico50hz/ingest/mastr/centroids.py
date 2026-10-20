"""`config/centroids.yaml`: formato que lee `load_points` (`ingest/ifs/extract.py`).

Cada punto lleva `id`, `lat`, `lon`, `kind` (`land`/`sea`) y `note`, más campos extra por
clúster (`capacity_mw`, `n_units`). Se escribe en JSON-en-YAML con escapes ASCII para que el
fichero sea legible con cualquier codificación por defecto.
"""

import datetime as dt
import json
from pathlib import Path

import polars as pl
import yaml

HEADER = """\
# Centroides reales de F2.6 (MaStR), calculados con `python -m predico50hz.ingest.mastr`.
# Formato de `load_points`; `capacity_mw` y `n_units` son extra y los
# ignora `load_points`. Pesos = capacidad ACTUAL de la zona de control de 50Hertz.
"""


def write_centroids(clusters: pl.DataFrame, meta: dict[str, object], path: Path) -> None:
    lines = [HEADER.rstrip("\n")]
    for k, v in meta.items():
        lines.append(f"{k}: {json.dumps(v)}")
    lines.append("provisional: false")
    lines.append("points:")
    for r in clusters.sort("kind", "cluster_id").iter_rows(named=True):
        item = {
            "id": r["cluster_id"],
            "lat": round(r["lat"], 4),
            "lon": round(r["lon"], 4),
            "kind": r["kind"],
            "capacity_mw": round(r["capacity_mw"], 1),
            "n_units": int(r["n_units"]),
            "note": r["note"],
        }
        lines.append("  - " + json.dumps(item))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    yaml.safe_load(path.read_text())  # falla si el YAML resultante no es válido


def stamp(export_date: str, k: int, seed: int) -> dict[str, object]:
    return {
        "source": "MaStR Gesamtdatenexport (Bundesnetzagentur, DL-DE-BY-2.0)",
        "export_date": export_date,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "k_land": k,
        "seed": seed,
    }
