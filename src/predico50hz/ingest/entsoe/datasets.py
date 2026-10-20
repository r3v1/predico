# ruff: noqa: PLR2004
"""Catálogo de datasets de ENTSO-E y troceado de las peticiones."""

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

CONTROL_AREA = "10YDE-VE-------2"  # 50Hertz
BIDDING_ZONE = "10Y1001A1001A82H"  # DE-LU
WIND_OFFSHORE = "B18"
WIND_ONSHORE = "B19"

DEFAULT_BRONZE = "data/bronze/entsoe"
DEFAULT_SILVER = "data/silver/entsoe"
DEFAULT_VALIDATION = "data/validation"


@dataclass(frozen=True)
class Dataset:
    name: str
    params: dict[str, str]
    variants: tuple[str, ...]  # un valor de psrType por petición; ("",) = sin filtro
    start: dt.date  # inicio de la historia disponible/pedida
    chunk: str  # "month" | "year"
    fallback_area: str | None = None  # zona alternativa si el área de control no tiene datos


DATASETS: dict[str, Dataset] = {
    "generation": Dataset(
        "generation",
        {"documentType": "A75", "processType": "A16", "in_Domain": CONTROL_AREA},
        (WIND_OFFSHORE, WIND_ONSHORE),
        dt.date(2015, 1, 1),
        "month",
    ),
    "capacity": Dataset(
        "capacity",
        {"documentType": "A68", "processType": "A33", "in_Domain": CONTROL_AREA},
        (WIND_OFFSHORE, WIND_ONSHORE),
        dt.date(2015, 1, 1),
        "year",
        fallback_area=BIDDING_ZONE,
    ),
    "prices": Dataset(
        "prices",
        {
            "documentType": "A44",
            "in_Domain": BIDDING_ZONE,
            "out_Domain": BIDDING_ZONE,
            "contract_MarketAgreement.type": "A01",
            # posición 1 = SDAC (la subasta de 15 min de EPEX es la posición 2)
            "classificationSequence_AttributeInstanceComponent.position": "1",
        },
        ("",),
        dt.date(2018, 10, 1),
        "month",
    ),
    "forecast": Dataset(
        "forecast",
        {"documentType": "A69", "processType": "A01", "in_Domain": CONTROL_AREA},
        (WIND_OFFSHORE, WIND_ONSHORE),
        dt.date(2015, 1, 1),
        "month",
    ),
}


def request_params(ds: Dataset, variant: str, area: str | None = None) -> dict[str, str]:
    params = dict(ds.params)
    if variant:
        params["psrType"] = variant
    if area:
        params["in_Domain"] = area
    return params


def windows(start: dt.date, end: dt.date, chunk: str) -> Iterator[tuple[dt.datetime, dt.datetime]]:
    """Ventanas [inicio, fin) en UTC, alineadas a mes o año natural, que cubren start..end."""
    cur = start.replace(day=1, month=1) if chunk == "year" else start.replace(day=1)
    lo = start
    while cur < end:
        nxt = (
            cur.replace(year=cur.year + 1)
            if chunk == "year"
            else (
                cur.replace(year=cur.year + 1, month=1)
                if cur.month == 12
                else cur.replace(month=cur.month + 1)
            )
        )
        a, b = max(cur, lo), min(nxt, end)
        if a < b:
            yield (
                dt.datetime.combine(a, dt.time(), dt.UTC),
                dt.datetime.combine(b, dt.time(), dt.UTC),
            )
        cur = nxt
