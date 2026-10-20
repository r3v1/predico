# ruff: noqa: PLR2004, PLW2901
"""Genera las fronteras simplificadas del informe a partir de Natural Earth (dominio público).

Uso (una sola vez; el resultado se versiona en `src/predico50hz/report/vendor/geo/`):

    uv run python scripts/build_report_geo.py <directorio_con_los_geojson_de_natural_earth>

Entradas (Natural Earth v5.1.2, `geojson/` del repositorio `nvkelso/natural-earth-vector`):
`ne_10m_admin_1_states_provinces.geojson`, `ne_10m_admin_0_countries.geojson` y
`ne_110m_admin_0_countries.geojson`. Salidas: `zone.geojson` (Länder alemanes y países vecinos,
recortados a la vista del mapa) y `world.geojson` (mapamundi).
"""

import json
import math
import sys
from pathlib import Path

Ring = list[tuple[float, float]]

VIEW = (6.8, 47.2, 18.6, 57.6)  # lon_min, lat_min, lon_max, lat_max de la vista de la zona
ZONE_STATES = {"Berlin", "Brandenburg", "Mecklenburg-Vorpommern", "Sachsen", "Sachsen-Anhalt"}
ZONE_STATES.add("Thüringen")
OUT = Path("src/predico50hz/report/vendor/geo")


def _dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def simplify(pts: Ring, tol: float) -> Ring:
    """Douglas-Peucker iterativo."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        far, idx = 0.0, -1
        for k in range(i + 1, j):
            d = _dist(pts[k], pts[i], pts[j])
            if d > far:
                far, idx = d, k
        if far > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep, strict=True) if k]


def clip(ring: Ring, box: tuple[float, float, float, float]) -> Ring:
    """Sutherland-Hodgman contra un rectángulo."""
    x0, y0, x1, y1 = box
    edges = [
        (
            lambda p: p[0] >= x0,
            lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0])),
        ),
        (
            lambda p: p[0] <= x1,
            lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0])),
        ),
        (
            lambda p: p[1] >= y0,
            lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0),
        ),
        (
            lambda p: p[1] <= y1,
            lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1),
        ),
    ]
    out = ring[:-1] if ring and ring[0] == ring[-1] else ring
    for inside, cross in edges:
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        if not out:
            return []
    return [*out, out[0]]


def polygons(geom: dict) -> list[list[Ring]]:
    if geom["type"] == "Polygon":
        return [[[tuple(p[:2]) for p in r] for r in geom["coordinates"]]]
    return [[[tuple(p[:2]) for p in r] for r in poly] for poly in geom["coordinates"]]


def process(geom: dict, tol: float, box: tuple[float, float, float, float] | None) -> dict | None:
    polys: list[list[list[list[float]]]] = []
    for poly in polygons(geom):
        rings: list[list[list[float]]] = []
        for r in poly:
            r = clip(r, box) if box else r
            r = simplify(r, tol) if r else r
            if len(r) >= 4:
                rings.append([[round(x, 3), round(y, 3)] for x, y in r])
        if rings:
            polys.append(rings)
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


def main(src: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    feats = []
    admin1 = json.loads((src / "ne_10m_admin_1_states_provinces.geojson").read_text("utf-8"))
    for f in admin1["features"]:
        p = f["properties"]
        if p["admin"] != "Germany":
            continue
        g = process(f["geometry"], 0.012, VIEW)
        if g:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": g,
                    "properties": {
                        "name": p["name"],
                        "kind": "land",
                        "zone": p["name"] in ZONE_STATES,
                    },
                }
            )
    admin0 = json.loads((src / "ne_10m_admin_0_countries.geojson").read_text("utf-8"))
    for f in admin0["features"]:
        p = f["properties"]
        if p["ADMIN"] == "Germany":
            continue
        g = process(f["geometry"], 0.02, VIEW)
        if g:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": g,
                    "properties": {"name": p["ADMIN"], "kind": "country", "zone": False},
                }
            )
    (OUT / "zone.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": feats},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        "utf-8",
    )
    world = json.loads((src / "ne_110m_admin_0_countries.geojson").read_text("utf-8"))
    wf = []
    for f in world["features"]:
        g = process(f["geometry"], 0.15, None)
        if g and f["properties"]["ADMIN"] != "Antarctica":
            wf.append(
                {"type": "Feature", "geometry": g, "properties": {"name": f["properties"]["ADMIN"]}}
            )
    (OUT / "world.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": wf}, separators=(",", ":"), ensure_ascii=False
        ),
        "utf-8",
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
