"""Unidades eólicas tipadas: catálogos resueltos, zona, coordenadas con proxy y estado.

Entrada: tablas de `extract` (texto). Salida: la plata `units` (una fila por unidad que no está
solo "en planificación"; Alemania entera, con `in_zone` marcando la zona de 50Hertz).
"""

import polars as pl

from predico50hz.ingest.mastr.zone import assign_zone

# Alemania incluida la ZEE (para detectar coordenadas absurdas o con ejes cambiados).
GER_LAT = (47.0, 55.5)
GER_LON = (5.0, 15.6)
KW = 1000.0
PROXY_LEVELS = (
    ("plz", ["postal_code"]),
    ("gemeinde", ["municipality_key"]),
    ("landkreis", ["bundesland", "landkreis"]),
    ("bundesland", ["bundesland"]),
)
UNIT_COLUMNS = [
    "unit_id", "location_id", "name", "windpark", "kind", "sea_area", "bundesland", "landkreis",
    "municipality", "municipality_key", "postal_code", "lat", "lon", "coord_source",
    "lat_raw", "lon_raw", "gross_mw", "net_mw", "start_date", "end_date", "temp_stop_start",
    "temp_resume", "registered_date", "status", "hub_height_m", "rotor_diameter_m",
    "manufacturer", "model", "regelzone", "regelzone_unit", "zone_rule", "zone_by_state",
    "zone_doubtful", "in_zone",
]  # fmt: skip


def _catalog(cat: dict[str, str], col: str) -> pl.Expr:
    return pl.col(col).replace_strict(cat, default=None, return_dtype=pl.String)


def _date(col: str) -> pl.Expr:
    return pl.col(col).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False)


def typed_units(raw: pl.DataFrame, cat: dict[str, str], nap: pl.DataFrame) -> pl.DataFrame:
    """Unidades eólicas del export con tipos, catálogos y Regelzone; sin las "en planificación"."""
    zone_by_loc = (
        nap.drop_nulls("regelzone_id")
        .group_by("location")
        .agg(pl.col("regelzone_id").first())
        .select(pl.col("location").alias("location_id"), "regelzone_id")
    )
    df = (
        raw.rename({"EinheitMastrNummer": "unit_id", "LokationMaStRNummer": "location_id"})
        .join(zone_by_loc, on="location_id", how="left")
        .select(
            "unit_id",
            "location_id",
            pl.col("NameStromerzeugungseinheit").alias("name"),
            pl.col("NameWindpark").alias("windpark"),
            (pl.col("WindAnLandOderAufSee") == _id(cat, "Windenergie auf See")).alias(
                "is_offshore"
            ),
            _catalog(cat, "Seelage").alias("sea_area"),
            _catalog(cat, "Bundesland").alias("bundesland"),
            pl.col("Landkreis").alias("landkreis"),
            pl.col("Gemeinde").alias("municipality"),
            pl.col("Gemeindeschluessel").alias("municipality_key"),
            pl.col("Postleitzahl").alias("postal_code"),
            pl.col("Breitengrad").cast(pl.Float64, strict=False).alias("lat_raw"),
            pl.col("Laengengrad").cast(pl.Float64, strict=False).alias("lon_raw"),
            (pl.col("Bruttoleistung").cast(pl.Float64, strict=False) / KW).alias("gross_mw"),
            (pl.col("Nettonennleistung").cast(pl.Float64, strict=False) / KW).alias("net_mw"),
            _date("Inbetriebnahmedatum").alias("start_date"),
            _date("DatumEndgueltigeStilllegung").alias("end_date"),
            _date("DatumBeginnVoruebergehendeStilllegung").alias("temp_stop_start"),
            _date("DatumWiederaufnahmeBetrieb").alias("temp_resume"),
            _date("Registrierungsdatum").alias("registered_date"),
            _catalog(cat, "EinheitBetriebsstatus").alias("status"),
            pl.col("Nabenhoehe").cast(pl.Float64, strict=False).alias("hub_height_m"),
            pl.col("Rotordurchmesser").cast(pl.Float64, strict=False).alias("rotor_diameter_m"),
            pl.col("Hersteller").alias("manufacturer"),
            pl.col("Typenbezeichnung").alias("model"),
            _catalog(cat, "regelzone_id").alias("regelzone"),
            _catalog(cat, "VorlaeufigeRegelzonenDerEinheit").alias("regelzone_unit"),
        )
    )
    df = df.filter(pl.col("status") != "In Planung")
    df = df.with_columns(
        kind=pl.when(pl.col("is_offshore")).then(pl.lit("sea")).otherwise(pl.lit("land"))
    )
    return assign_zone(df).drop("is_offshore")


def _id(cat: dict[str, str], value: str) -> str:
    return next(k for k, v in cat.items() if v == value)


def valid_coords(lat: pl.Expr, lon: pl.Expr) -> pl.Expr:
    return (lat.is_between(*GER_LAT) & lon.is_between(*GER_LON)).fill_null(False)


def resolve_coordinates(df: pl.DataFrame) -> pl.DataFrame:
    """`lat`, `lon` y `coord_source`: coordenada de la unidad o proxy (PLZ > municipio > …).

    Proxy = media ponderada por potencia neta de las unidades **del mismo tipo (tierra/mar)** con
    coordenadas válidas en esa PLZ, municipio, Landkreis o Bundesland. Se calcula con todas las
    unidades de Alemania, no solo las de la zona (la PLZ identifica un lugar único).
    """
    ok = valid_coords(pl.col("lat_raw"), pl.col("lon_raw"))
    # Eje cambiado (lat/lon intercambiados): se rescata si así cae dentro de Alemania.
    swapped = ~ok & valid_coords(pl.col("lon_raw"), pl.col("lat_raw"))
    df = df.with_columns(
        lat=pl.when(ok).then("lat_raw").when(swapped).then("lon_raw"),
        lon=pl.when(ok).then("lon_raw").when(swapped).then("lat_raw"),
        coord_source=pl.when(ok)
        .then(pl.lit("unit"))
        .when(swapped)
        .then(pl.lit("unit_swapped"))
        .otherwise(pl.lit(None, dtype=pl.String)),
    )
    for source, keys in PROXY_LEVELS:
        have = df.filter(
            pl.col("lat").is_not_null() & pl.col("coord_source").is_in(["unit", "unit_swapped"])
        )
        w = pl.col("net_mw").clip(lower_bound=1e-6)
        ref = (
            have.drop_nulls(keys)
            .group_by(["kind", *keys])
            .agg(
                ((pl.col("lat") * w).sum() / w.sum()).alias("_plat"),
                ((pl.col("lon") * w).sum() / w.sum()).alias("_plon"),
            )
        )
        df = (
            df.join(ref, on=["kind", *keys], how="left", nulls_equal=False)
            .with_columns(
                lat=pl.coalesce("lat", "_plat"),
                lon=pl.coalesce("lon", "_plon"),
                coord_source=pl.when(
                    pl.col("coord_source").is_null() & pl.col("_plat").is_not_null()
                )
                .then(pl.lit(source))
                .otherwise("coord_source"),
            )
            .drop("_plat", "_plon")
        )
    return df
