# ruff: noqa: PLR2004, RUF002
"""Generadores deterministas de los datos de entrada de la cadena de extremo a extremo.

Todo sale de una función de viento «verdadero» (`wind_truth`) que se vende de tres formas:

- el NWP sintético (un `Source` en memoria por pasada) la lleva como viento previsto, con un
  sesgo por pasada y dispersión entre miembros;
- la generación de ENTSO-E (respuestas XML de un transporte HTTP falso) es la capacidad de
  MaStR por una curva de potencia cúbica de esa misma función, con ruido;
- el «operador» (pronóstico day-ahead) es esa generación con un error aleatorio.

Así la relación viento → generación es clara y un modelo bien entrenado debe superar a la
climatología. Nada de red ni de `data/`: todo en memoria o en `tmp_path`.
"""

import datetime as dt
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt
import polars as pl
import xarray as xr

from predico50hz.ingest.dynamical.config import LEAD_HOURS, N_MEMBERS, VARIABLES, Domain
from predico50hz.ingest.dynamical.pipeline import PassMetrics
from predico50hz.ingest.dynamical.source import Source

Array = npt.NDArray[np.float64]

EPOCH = np.datetime64("2026-01-01T00:00:00", "ns")
# Dominio NWP pequeño que contiene todos los centroides sintéticos.
LAT_AXIS = np.arange(54.5, 51.49, -0.25)  # descendente, como el origen real
LON_AXIS = np.arange(10.5, 14.51, 0.25)
CAP_FACTOR_REF = 11.5  # m/s a los que la curva cúbica alcanza la capacidad
SEA_BOOST = 1.07  # el viento marino rinde algo más


# --------------------------------------------------------------------------------------
# Viento verdadero y curva de potencia
# --------------------------------------------------------------------------------------
def wind_truth(t: npt.NDArray[np.datetime64]) -> Array:
    """Viento (m/s) en función del instante UTC: varias oscilaciones sinoidales de 2 a 8 días."""
    h = (t.astype("datetime64[ns]") - EPOCH) / np.timedelta64(1, "h")
    w = (
        8.0
        + 3.2 * np.sin(2 * np.pi * h / (24 * 4.1) + 0.7)
        + 2.1 * np.sin(2 * np.pi * h / (24 * 2.3) + 2.1)
        + 1.4 * np.sin(2 * np.pi * h / (24 * 7.9))
        + 0.8 * np.sin(2 * np.pi * h / 24.0 + 1.0)
    )
    return np.maximum(w, 0.5)


def capacity_factor(w: Array, sea: bool) -> Array:
    """Curva de potencia cúbica saturada en [0, 1]."""
    ws = w * (SEA_BOOST if sea else 1.0)
    return np.clip((ws / CAP_FACTOR_REF) ** 3, 0.0, 1.0)


# --------------------------------------------------------------------------------------
# NWP: un origen «dynamical» falso por pasada
# --------------------------------------------------------------------------------------
def nwp_source(day: dt.date, seed: int = 100) -> Source:
    """Origen en memoria con la pasada 00Z de `day` (13 pasos de 3 h, 51 miembros).

    Respeta la forma del origen real que lee `read_cube`: latitud descendente, `init_time`,
    `lead_time` (timedelta), `ensemble_member` y las 10 variables de `VARIABLES`.
    """
    rng = np.random.default_rng(seed + day.toordinal())
    init = np.array([np.datetime64(day.isoformat(), "ns")])
    lead = np.array([h * np.timedelta64(1, "h") for h in LEAD_HOURS]).astype("timedelta64[ns]")
    valid = init[0] + lead
    bias = float(rng.normal(0.0, 0.7))  # error sistemático de esta pasada
    speed = wind_truth(valid)[:, None] + bias + rng.normal(0.0, 0.6, (len(lead), N_MEMBERS))
    speed = np.maximum(speed, 0.1)  # (lead, member)
    # Mismo viento en toda la malla con un gradiente suave hacia el norte (el mar rinde más).
    spatial = 1.0 + 0.08 * (LAT_AXIS - 53.0)[:, None] + 0.0 * LON_AXIS[None, :]
    s100 = speed[:, :, None, None] * spatial[None, None]
    s10 = 0.7 * s100
    theta = np.deg2rad(240.0)
    hours = ((valid - EPOCH) / np.timedelta64(1, "h"))[:, None, None, None]
    shape = s100.shape
    fields: dict[str, Array] = {
        "wind_u_100m": s100 * np.cos(theta),
        "wind_v_100m": s100 * np.sin(theta),
        "wind_u_10m": s10 * np.cos(theta),
        "wind_v_10m": s10 * np.sin(theta),
        "wind_gust_10m": 1.5 * s10,
        "temperature_2m": 8.0 + 4.0 * np.sin(2 * np.pi * hours / 24.0) + np.zeros(shape),
        "temperature_925hpa": 6.0 + np.zeros(shape),
        "temperature_850hpa": 3.0 + np.zeros(shape),
        "pressure_surface": 100_500.0 + np.zeros(shape),
        "pressure_reduced_to_mean_sea_level": 101_300.0 + np.zeros(shape),
    }
    dims = ("init_time", "lead_time", "ensemble_member", "latitude", "longitude")
    ds = xr.Dataset(
        {v: (dims, fields[v][None].astype(np.float32)) for v in VARIABLES},
        coords={
            "init_time": init,
            "lead_time": lead,
            "ensemble_member": np.arange(N_MEMBERS),
            "latitude": LAT_AXIS,
            "longitude": LON_AXIS,
        },
        attrs={"dataset_version": "0.1.0"},
    )
    return Source(ds, "fake://dynamical", f"SNAP-{day.isoformat()}", "0.1.0")


# --------------------------------------------------------------------------------------
# MaStR: un ZIP de export con XML UTF-16 como el real
# --------------------------------------------------------------------------------------
CATALOG = {
    "35": "In Betrieb",
    "31": "In Planung",
    "37": "Endgültig stillgelegt",
    "888": "Windenergie an Land",
    "889": "Windenergie auf See",
    "1400": "Brandenburg",
    "1401": "Mecklenburg-Vorpommern",
    "1402": "Niedersachsen",
    "1416": "AWZ",
    "639": "Ostsee",
    "9001": "50Hertz",
    "9002": "TenneT",
}
_ID = {v: k for k, v in CATALOG.items()}
LAND_CENTERS = [(53.45, 12.1), (52.75, 13.2), (52.55, 11.7), (53.15, 13.35)]


def _xml(tag: str, records: list[dict[str, str]]) -> bytes:
    """XML plano de una línea, UTF-16 con BOM, un registro por `tag` (formato del export)."""
    body = "".join(
        f"<{tag}>" + "".join(f"<{k}>{v}</{k}>" for k, v in r.items()) + f"</{tag}>" for r in records
    )
    return f"<?xml version='1.0' encoding='UTF-16'?><{tag}s>{body}</{tag}s>".encode("utf-16")


def _land_unit(
    i: int, lat: float, lon: float, net_kw: int, start: str, **extra: str
) -> dict[str, str]:
    return {
        "EinheitMastrNummer": f"SEE{i:05d}",
        "LokationMaStRNummer": f"SEL{i:05d}",
        "NameWindpark": f"Windpark {i % 17}",
        "Bundesland": _ID["Brandenburg"],
        "Landkreis": "Kreis X",
        "Breitengrad": f"{lat:.5f}",
        "Laengengrad": f"{lon:.5f}",
        "Inbetriebnahmedatum": start,
        "EinheitBetriebsstatus": _ID["In Betrieb"],
        "WindAnLandOderAufSee": _ID["Windenergie an Land"],
        "Bruttoleistung": str(int(net_kw * 1.05)),
        "Nettonennleistung": str(net_kw),
        "Nabenhoehe": "119",
        "Rotordurchmesser": "130",
        **extra,
    }


def write_mastr_export(path: Path, seed: int = 11) -> int:
    """Escribe `Gesamtdatenexport_*.zip` mínimo; devuelve las unidades de la zona 50Hertz.

    Contiene: 4 núcleos terrestres de 30 unidades, 6 unidades que entran en servicio durante
    el periodo de prueba (la capacidad crece), 2 que se dan de baja, unidades fuera de zona
    (Niedersachsen, TenneT) y 2 parques marinos de Báltico; la Regelzone de algunas sale del
    punto de conexión (`Netzanschlusspunkte_*.xml`) y el resto de la regla por Bundesland.
    """
    rng = np.random.default_rng(seed)
    units: list[dict[str, str]] = []
    naps: list[dict[str, str]] = []
    n_zone = 0
    i = 0
    for lat0, lon0 in LAND_CENTERS:
        for _ in range(30):
            lat = float(np.clip(lat0 + rng.normal(0, 0.1), 52.3, 53.7))
            lon = float(np.clip(lon0 + rng.normal(0, 0.1), 11.3, 13.7))
            year = int(rng.integers(2016, 2025))
            u = _land_unit(i, lat, lon, int(rng.integers(2000, 6000)), f"{year}-06-15")
            if i % 3 == 0:  # Regelzone oficial del punto de conexión
                naps.append(
                    {
                        "LokationMaStRNummer": u["LokationMaStRNummer"],
                        "NetzanschlusspunktMastrNummer": f"SNA{i:05d}",
                        "RegelzoneNetzanschlusspunkt": _ID["50Hertz"],
                    }
                )
            units.append(u)
            n_zone += 1
            i += 1
    for start in (
        "2026-04-10",
        "2026-04-28",
        "2026-05-20",
        "2026-06-05",
        "2026-06-20",
        "2026-07-15",
    ):
        units.append(_land_unit(i, 53.0, 12.5, 4500, start))
        n_zone += 1
        i += 1
    for k in range(2):  # bajas definitivas durante el periodo
        units.append(
            _land_unit(
                i,
                53.2,
                12.0 + 0.1 * k,
                5000,
                "2017-03-01",
                EinheitBetriebsstatus=_ID["Endgültig stillgelegt"],
                DatumEndgueltigeStilllegung="2026-05-12",
            )
        )
        n_zone += 1
        i += 1
    for k in range(4):  # fuera de zona: Niedersachsen y un punto de conexión de TenneT
        extra = {"Bundesland": _ID["Niedersachsen"]} if k < 3 else {}
        u = _land_unit(i, 52.4, 11.4, 3000, "2018-01-01", **extra)
        if k == 3:
            naps.append(
                {
                    "LokationMaStRNummer": u["LokationMaStRNummer"],
                    "NetzanschlusspunktMastrNummer": f"SNA{i:05d}",
                    "RegelzoneNetzanschlusspunkt": _ID["TenneT"],
                }
            )
        units.append(u)
        i += 1
    for park, (lat, lon, year) in {
        "Alpha": (53.85, 13.10, 2019),
        "Beta": (53.90, 13.80, 2021),
    }.items():
        for k in range(6):
            units.append(
                {
                    "EinheitMastrNummer": f"SEE{i:05d}",
                    "LokationMaStRNummer": f"SEL{i:05d}",
                    "NameWindpark": f"OWP {park} Ostsee",
                    "Bundesland": _ID["AWZ"],
                    "Seelage": _ID["Ostsee"],
                    "Breitengrad": f"{lat + 0.01 * k:.5f}",
                    "Laengengrad": f"{lon:.5f}",
                    "Inbetriebnahmedatum": f"{year}-09-01",
                    "EinheitBetriebsstatus": _ID["In Betrieb"],
                    "WindAnLandOderAufSee": _ID["Windenergie auf See"],
                    "Bruttoleistung": "8400",
                    "Nettonennleistung": "8000",
                }
            )
            n_zone += 1
            i += 1
    catalog = [{"Id": k, "Wert": v} for k, v in CATALOG.items()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Katalogwerte.xml", _xml("Katalogwert", catalog))
        zf.writestr("EinheitenWind.xml", _xml("EinheitWind", units))
        zf.writestr("Netzanschlusspunkte_1.xml", _xml("Netzanschlusspunkt", naps))
    return n_zone


# --------------------------------------------------------------------------------------
# ENTSO-E: una «API» falsa que contesta XML por documentType y psrType
# --------------------------------------------------------------------------------------
SERIES_START = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
SERIES_END = dt.datetime(2026, 8, 5, tzinfo=dt.UTC)
OFFSHORE, ONSHORE = "B18", "B19"


class FakeEntsoe:
    """Series de generación y de pronóstico del operador sobre la malla de 15 min UTC.

    `generation(psr)` = capacidad MaStR del día × curva de potencia de `wind_truth` + ruido;
    `forecast(psr)` = esa generación con un error aleatorio (el «operador»). `scale` permite
    servir una primera descarga errónea para comprobar que la plata se queda con la última.
    """

    def __init__(self, cap_total: pl.DataFrame, seed: int = 7) -> None:
        rng = np.random.default_rng(seed)
        self.ts = np.arange(
            np.datetime64(SERIES_START.replace(tzinfo=None), "ns"),
            np.datetime64(SERIES_END.replace(tzinfo=None), "ns"),
            np.timedelta64(15, "m"),
        )
        n = len(self.ts)
        w = wind_truth(self.ts + np.timedelta64(450, "s"))
        days = self.ts.astype("datetime64[D]")
        self._gen: dict[str, Array] = {}
        self._fc: dict[str, Array] = {}
        for psr, kind in ((OFFSHORE, "sea"), (ONSHORE, "land")):
            cap = cap_total.filter(pl.col("kind") == kind).sort("date")
            by_day = dict(
                zip(
                    cap["date"].cast(pl.String).to_list(), cap["capacity_mw"].to_list(), strict=True
                )
            )
            mw = np.array([by_day[str(d)] for d in days])
            smooth = np.convolve(rng.normal(0, 0.04, n), np.ones(8) / 8, mode="same")
            cf = np.clip(capacity_factor(w, kind == "sea") + smooth, 0.0, 1.0)
            gen = cf * mw
            self._gen[psr] = np.round(gen, 3)
            err = np.convolve(rng.normal(0, 0.10, n), np.ones(16) / 4, mode="same")
            self._fc[psr] = np.round(np.clip(gen * (1.0 + err), 0.0, mw), 3)
        self.scale = 1.0

    def _slice(self, store: dict[str, Array], psr: str, a: dt.datetime, b: dt.datetime) -> Array:
        lo = int(np.searchsorted(self.ts, np.datetime64(a.replace(tzinfo=None), "ns")))
        hi = int(np.searchsorted(self.ts, np.datetime64(b.replace(tzinfo=None), "ns")))
        return store[psr][lo:hi]

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params
        a = dt.datetime.strptime(q["periodStart"], "%Y%m%d%H%M").replace(tzinfo=dt.UTC)
        b = dt.datetime.strptime(q["periodEnd"], "%Y%m%d%H%M").replace(tzinfo=dt.UTC)
        doc_type, psr = q["documentType"], q["psrType"]
        store = {"A75": self._gen, "A69": self._fc}[doc_type]
        vals = self._slice(store, psr, a, b) * self.scale
        return httpx.Response(200, content=entsoe_xml(psr, a, b, vals, doc_type))


def entsoe_xml(
    psr: str, start: dt.datetime, end: dt.datetime, values: Array, doc_type: str
) -> bytes:
    """`GL_MarketDocument` con una serie PT15M completa (`quantity` por posición)."""
    fmt = "%Y-%m-%dT%H:%MZ"
    pts = "".join(
        f"<Point><position>{k}</position><quantity>{v}</quantity></Point>"
        for k, v in enumerate(values, start=1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
<type>{doc_type}</type><revisionNumber>1</revisionNumber>
<createdDateTime>{end:%Y-%m-%dT%H:%M:%SZ}</createdDateTime>
<TimeSeries><curveType>A01</curveType><MktPSRType><psrType>{psr}</psrType></MktPSRType>
<Period><timeInterval><start>{start:{fmt}}</start><end>{end:{fmt}}</end></timeInterval>
<resolution>PT15M</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>""".encode()


# --------------------------------------------------------------------------------------
# Parámetros de la cadena y contenedor de sus salidas
# --------------------------------------------------------------------------------------
EXPORT_DATE = "20260805"
# Cierres con pasada: 26-mar..30-jul cada 2 días (incluye el día de 92 pasos, 28-mar) y el
# 31-jul, cuyo día objetivo (1-ago) cae en el periodo reservado.
FIRST_ISSUE = dt.date(2026, 3, 26)
LAST_ISSUE = dt.date(2026, 7, 31)
STRIDE_DAYS = 2
DOMAIN = Domain(52.0, 54.0, 11.0, 14.0)
MIN_TRAIN_MONTHS = 2  # con datos desde finales de marzo: pliegues de mayo, junio y julio
FETCH_START = dt.date(2026, 3, 1)
FETCH_END = dt.date(2026, 8, 3)


def issue_dates() -> list[dt.date]:
    days = [
        FIRST_ISSUE + dt.timedelta(days=k)
        for k in range(0, (LAST_ISSUE - FIRST_ISSUE).days + 1, STRIDE_DAYS)
    ]
    return sorted({*days, LAST_ISSUE})


@dataclass
class Chain:
    root: Path
    n_units_zone: int = 0
    mastr_report: dict[str, Any] = field(default_factory=dict)
    nwp_metrics: list[PassMetrics] = field(default_factory=list)
    entsoe_info: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    entsoe_reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    gold_rc: int = -1
    fake: FakeEntsoe | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def silver(self) -> Path:
        return self.root / "silver"

    @property
    def centroids(self) -> Path:
        return self.root / "centroids.yaml"

    @property
    def nwp_silver(self) -> Path:
        return self.silver / "ifs_ens_clusters"

    @property
    def nwp_bronze(self) -> Path:
        return self.root / "bronze" / "dynamical"

    @property
    def gold(self) -> Path:
        return self.root / "gold" / "features"

    @property
    def validation(self) -> Path:
        return self.root / "validation"

    @property
    def backtest(self) -> Path:
        return self.root / "backtest"
