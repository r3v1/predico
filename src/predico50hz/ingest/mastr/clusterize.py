# pyright: basic
"""Construcción de los clústeres: elección de k, centroides terrestres y grupos marinos.

Terrestre: k-means ponderado por la capacidad ACTUAL (en operación o parada temporal).
`k` es el menor del rango que cumple, a la vez, un error de representación (RMSE ponderado de la
distancia unidad-centroide) no mayor que el lado mayor de la malla NWP de 0,25° (≈ 27,8 km:
por debajo de esa escala la malla IFS no distingue puntos), centroides separados al menos el
lado menor de la malla (≈ 16,7 km, sin dos centroides en la misma celda) y estabilidad entre
semillas (índice de Rand ≥ 0,95).

Marino: un clúster por parque (`windpark`), fusionando (enlace simple) los parques cuyos
centroides distan menos de media celda NWP (lado menor, ≈ 8,3 km): con la interpolación
bilineal reciben prácticamente los mismos pesos de la malla y no aportan información distinta.
"""

import datetime as dt
import math
import re
import unicodedata
from dataclasses import dataclass

import numpy as np
import polars as pl

from predico50hz.ingest.mastr.clusters import (
    KM_PER_DEG,
    KMeansResult,
    agreement,
    assign_nearest,
    from_km,
    group_by_proximity,
    kmeans_weighted,
    min_center_distance,
    to_km,
)
from predico50hz.ingest.mastr.config import LAT_RANGE, LON_RANGE

GRID_DEG = 0.25
GRID_NS_KM = GRID_DEG * KM_PER_DEG  # lado mayor de la celda a ~53° N
GRID_EW_KM = GRID_NS_KM * math.cos(math.radians(53.0))  # lado menor
SEA_MERGE_KM = GRID_EW_KM / 2
OPERATING = ("In Betrieb", "Vorübergehend stillgelegt")
STATE_CODE = {
    "Berlin": "be",
    "Brandenburg": "bb",
    "Hamburg": "hh",
    "Mecklenburg-Vorpommern": "mv",
    "Niedersachsen": "ni",
    "Sachsen": "sn",
    "Sachsen-Anhalt": "st",
    "Thüringen": "th",
}
K_RANGE = (15, 30)
SEED = 42
N_INIT = 10
MIN_ARI = 0.95


@dataclass(frozen=True)
class KRow:
    k: int
    rmse_km: float
    gain_pct: float | None
    min_sep_km: float
    stability_ari: float
    min_cluster_mw: float
    max_cluster_mw: float


def operating(units: pl.DataFrame, kind: str, on: dt.date | None = None) -> pl.DataFrame:
    """Unidades de la zona con coordenadas, del tipo `kind`, activas hoy (o en la fecha `on`)."""
    df = units.filter((pl.col("kind") == kind) & pl.col("in_zone") & pl.col("lat").is_not_null())
    if on is None:
        return df.filter(pl.col("status").is_in(OPERATING))
    return df.filter(
        (pl.col("start_date") <= on) & (pl.col("end_date").is_null() | (pl.col("end_date") > on))
    )


def _xyw(df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:  # pyright: ignore[reportMissingTypeArgument]
    xy = to_km(df["lat"].to_numpy(), df["lon"].to_numpy())
    return xy, df["net_mw"].to_numpy().astype(np.float64)


def k_table(
    xy: np.ndarray,  # pyright: ignore[reportMissingTypeArgument]
    w: np.ndarray,  # pyright: ignore[reportMissingTypeArgument]
    k_range: tuple[int, int] = K_RANGE,
    seed: int = SEED,
) -> list[KRow]:
    rows: list[KRow] = []
    prev: float | None = None
    for k in range(k_range[0], k_range[1] + 1):
        a = kmeans_weighted(xy, w, k, seed=seed, n_init=N_INIT)
        b = kmeans_weighted(xy, w, k, seed=seed + 1, n_init=N_INIT)
        mw = np.bincount(a.labels, weights=w, minlength=k)
        rows.append(
            KRow(
                k,
                a.rmse_km,
                None if prev is None else 100.0 * (prev - a.rmse_km) / prev,
                min_center_distance(a.centers),
                agreement(a.labels, b.labels),
                float(mw.min()),
                float(mw.max()),
            )
        )
        prev = a.rmse_km
    return rows


def choose_k(table: list[KRow]) -> int:
    """Menor k que cumple RMSE, separación y estabilidad; si ninguno, el mayor del rango."""
    for r in table:
        if r.rmse_km <= GRID_NS_KM and r.min_sep_km >= GRID_EW_KM and r.stability_ari >= MIN_ARI:
            return r.k
    return table[-1].k


def land_clusters(
    units: pl.DataFrame, k: int | None = None, seed: int = SEED, on: dt.date | None = None
) -> tuple[pl.DataFrame, KMeansResult, list[KRow]]:
    """(tabla de clústeres, resultado k-means, tabla de k) con los pesos de la fecha `on`."""
    cur = operating(units, "land", on)
    xy, w = _xyw(cur)
    table = k_table(xy, w, seed=seed) if k is None else []
    k = k or choose_k(table)
    res = kmeans_weighted(xy, w, k, seed=seed, n_init=N_INIT)
    lat, lon = from_km(res.centers)
    df = cur.with_columns(_label=pl.Series(res.labels))
    stats = df.group_by("_label").agg(
        capacity_mw=pl.col("net_mw").sum(),
        n_units=pl.len().cast(pl.Int64),
        state=pl.col("bundesland").filter(pl.col("bundesland").is_not_null()).mode().first(),
        county=pl.col("landkreis").filter(pl.col("landkreis").is_not_null()).mode().first(),
    )
    base = (
        pl.DataFrame({"_label": range(k), "lat": lat, "lon": lon})
        .join(stats, on="_label")
        .sort("state", "capacity_mw", descending=[False, True])
    )
    base = base.with_columns(
        n=pl.int_range(1, pl.len() + 1).over("state"),
    ).with_columns(
        cluster_id=pl.col("state").replace_strict(STATE_CODE, default="xx")
        + "_"
        + pl.col("n").cast(pl.String).str.zfill(2),
        kind=pl.lit("land"),
        note=pl.lit("Tierra, ")
        + pl.col("state")
        + pl.lit(", Landkreis dominante: ")
        + pl.col("county"),
    )
    return base.drop("_label", "n", "county"), res, table


def _slug(name: str) -> str:
    """`OWP Arkona Becken Süd-Ost` -> `arkona_becken` (ASCII, sin prefijo OWP, dos palabras)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", ascii_name) if w and w != "owp"]
    return "_".join(words[:2])


def sea_clusters(units: pl.DataFrame) -> pl.DataFrame:
    """Un clúster por parque marino en operación; los de menos de una celda NWP se fusionan."""
    cur = operating(units, "sea").with_columns(
        windpark=pl.col("windpark").fill_null(pl.col("unit_id"))
    )
    parks = (
        cur.group_by("windpark")
        .agg(
            lat=(pl.col("lat") * pl.col("net_mw")).sum() / pl.col("net_mw").sum(),
            lon=(pl.col("lon") * pl.col("net_mw")).sum() / pl.col("net_mw").sum(),
            capacity_mw=pl.col("net_mw").sum(),
            n_units=pl.len(),
        )
        .sort("windpark")
    )
    lab = group_by_proximity(to_km(parks["lat"].to_numpy(), parks["lon"].to_numpy()), SEA_MERGE_KM)
    parks = parks.with_columns(_g=pl.Series(lab))
    out: list[dict[str, object]] = []
    for _, g in parks.group_by("_g"):
        big = g.sort("capacity_mw", descending=True)
        mw = float(g["capacity_mw"].sum())
        out.append(
            {
                "lat": float((g["lat"] * g["capacity_mw"]).sum()) / mw,
                "lon": float((g["lon"] * g["capacity_mw"]).sum()) / mw,
                "capacity_mw": mw,
                "n_units": int(g["n_units"].sum()),
                "state": "Ostsee",
                "cluster_id": "sea_" + _slug(str(big["windpark"][0])),
                "kind": "sea",
                "note": "Mar (Báltico); parques: " + ", ".join(sorted(g["windpark"].to_list())),
            }
        )
    return pl.DataFrame(out).sort("capacity_mw", descending=True)


def in_domain(df: pl.DataFrame) -> pl.DataFrame:
    """Centroides fuera del dominio NWP (debe estar vacío)."""
    return df.filter(~pl.col("lat").is_between(*LAT_RANGE) | ~pl.col("lon").is_between(*LON_RANGE))


def assign_clusters(units: pl.DataFrame, clusters: pl.DataFrame) -> pl.DataFrame:
    """`cluster_id` de cada unidad: el centroide más cercano de su mismo tipo (tierra/mar)."""
    out = units.with_columns(cluster_id=pl.lit(None, dtype=pl.String))
    parts: list[pl.DataFrame] = []
    for kind in ("land", "sea"):
        c = clusters.filter(pl.col("kind") == kind)
        u = out.filter((pl.col("kind") == kind) & pl.col("lat").is_not_null() & pl.col("in_zone"))
        if u.is_empty() or c.is_empty():
            continue
        idx = assign_nearest(
            to_km(u["lat"].to_numpy(), u["lon"].to_numpy()),
            to_km(c["lat"].to_numpy(), c["lon"].to_numpy()),
        )
        parts.append(
            u.select("unit_id").with_columns(cid=pl.Series(c["cluster_id"].to_numpy()[idx]))
        )
    if not parts:
        return out
    m = pl.concat(parts)
    return out.join(m, on="unit_id", how="left").with_columns(cluster_id=pl.col("cid")).drop("cid")


def weight_sensitivity(
    units: pl.DataFrame, current: pl.DataFrame, k: int, dates: list[dt.date], seed: int = SEED
) -> list[dict[str, float | int | str]]:
    """Cuánto cambian los centroides terrestres si los pesos son los de otra fecha.

    Por fecha: desplazamiento (km) de cada centroide actual al más cercano de la nueva solución
    (media ponderada por capacidad y máximo), y RMSE de las unidades ACTUALES contra los
    centroides de esa fecha frente al RMSE óptimo actual (el coste de usar pesos antiguos).
    """
    cur = operating(units, "land")
    xy, w = _xyw(cur)
    c_cur = to_km(current["lat"].to_numpy(), current["lon"].to_numpy())
    base = kmeans_weighted(xy, w, k, seed=seed, n_init=N_INIT)
    out: list[dict[str, float | int | str]] = []
    for d in dates:
        past = operating(units, "land", d)
        pxy, pw = _xyw(past)
        alt = kmeans_weighted(pxy, pw, k, seed=seed, n_init=N_INIT)
        dist = np.sqrt(((c_cur[:, None, :] - alt.centers[None, :, :]) ** 2).sum(-1)).min(axis=1)
        cap = current["capacity_mw"].to_numpy()
        idx = assign_nearest(xy, alt.centers)
        rmse_alt = float(np.sqrt((w * ((xy - alt.centers[idx]) ** 2).sum(-1)).sum() / w.sum()))
        out.append(
            {
                "date": d.isoformat(),
                "n_units": past.height,
                "capacity_mw": float(pw.sum()),
                "shift_km_weighted_mean": float((dist * cap).sum() / cap.sum()),
                "shift_km_max": float(dist.max()),
                "rmse_current_units_km": rmse_alt,
                "rmse_optimal_km": base.rmse_km,
            }
        )
    return out
