"""Extracción del export de MaStR: unidades eólicas, puntos de conexión (Regelzone) y catálogos.

Solo se leen `EinheitenWind`, `Katalogwerte` y `Netzanschlusspunkte` (los 60 ficheros de puntos
de conexión suman 8,7 GB de XML, así que se procesan en paralelo y se filtran por las
ubicaciones de las unidades eólicas; el resto del export no se toca).
"""

import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import polars as pl

from predico50hz.ingest.mastr.xmlstream import iter_records, members

WIND_FIELDS = (
    "EinheitMastrNummer",
    "LokationMaStRNummer",
    "NameStromerzeugungseinheit",
    "NameWindpark",
    "GenMastrNummer",
    "Bundesland",
    "Landkreis",
    "Gemeinde",
    "Gemeindeschluessel",
    "Postleitzahl",
    "Ort",
    "Laengengrad",
    "Breitengrad",
    "Registrierungsdatum",
    "Inbetriebnahmedatum",
    "InbetriebnahmedatumAmAktuellenStandort",
    "GeplantesInbetriebnahmedatum",
    "DatumEndgueltigeStilllegung",
    "DatumBeginnVoruebergehendeStilllegung",
    "DatumWiederaufnahmeBetrieb",
    "EinheitBetriebsstatus",
    "EinheitSystemstatus",
    "WindAnLandOderAufSee",
    "Seelage",
    "Bruttoleistung",
    "Nettonennleistung",
    "Nabenhoehe",
    "Rotordurchmesser",
    "Hersteller",
    "Typenbezeichnung",
    "Technologie",
    "VorlaeufigeRegelzonenDerEinheit",
    "DatumLetzteAktualisierung",
)


def read_catalog(zf: zipfile.ZipFile) -> dict[str, str]:
    """Id -> texto de todos los valores de catálogo."""
    return {r["Id"]: r.get("Wert", "") for r in iter_records(zf, "Katalogwerte.xml", "Katalogwert")}


def read_wind_raw(zf: zipfile.ZipFile) -> pl.DataFrame:
    """Todas las unidades eólicas del export (texto sin interpretar, campos de `WIND_FIELDS`)."""
    rows = [
        {k: r.get(k) for k in WIND_FIELDS}
        for name in members(zf, "EinheitenWind")
        for r in iter_records(zf, name, "EinheitWind")
    ]
    return pl.DataFrame(rows, schema=dict.fromkeys(WIND_FIELDS, pl.String))


def _nap_member(args: tuple[str, str, frozenset[str]]) -> list[tuple[str, str, str | None]]:
    zip_path, name, locations = args
    out: list[tuple[str, str, str | None]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for r in iter_records(zf, name, "Netzanschlusspunkt"):
            loc = r.get("LokationMaStRNummer")
            if loc in locations:
                out.append(
                    (loc, r["NetzanschlusspunktMastrNummer"], r.get("RegelzoneNetzanschlusspunkt"))
                )
    return out


def read_grid_points(zip_path: Path, locations: set[str], workers: int = 16) -> pl.DataFrame:
    """(location, nap_id, regelzone_id) de los puntos de conexión de `locations`."""
    with zipfile.ZipFile(zip_path) as zf:
        names = members(zf, "Netzanschlusspunkte")
    jobs = [(str(zip_path), n, frozenset(locations)) for n in names]
    with ProcessPoolExecutor(workers) as pool:
        rows = [row for part in pool.map(_nap_member, jobs) for row in part]
    schema = {"location": pl.String, "nap_id": pl.String, "regelzone_id": pl.String}
    return pl.DataFrame(rows, schema=schema, orient="row")
