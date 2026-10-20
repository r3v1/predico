"""Descarga selectiva por rangos de bytes a partir del `.index` de ECMWF Open Data."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

from predico50hz.ingest.ifs.fields import ALL_FIELDS, DYNAMIC_FIELDS, LSM, Field
from predico50hz.ingest.ifs.paths import BASE_URL

MAX_RETRIES = 12
BACKOFF_CAP_S = 60.0
_STATUS_RETRY = {500, 502, 503, 504}


@dataclass(frozen=True)
class IndexEntry:
    param: str
    levtype: str
    levelist: int | None
    step: int
    offset: int
    length: int

    def matches(self, f: Field) -> bool:
        return self.param == f.param and self.levtype == f.levtype and self.levelist == f.levelist


@dataclass(frozen=True)
class ByteRange:
    """Rango contiguo `[start, end]` (inclusivo) que agrupa uno o varios mensajes."""

    start: int
    end: int
    entries: tuple[IndexEntry, ...]

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_index(text: str) -> list[IndexEntry]:
    """Parsea un `.index` (JSON por lineas)."""
    out: list[IndexEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        r: dict[str, Any] = json.loads(line)
        lv = r.get("levelist")
        out.append(
            IndexEntry(
                param=r["param"],
                levtype=r["levtype"],
                levelist=int(lv) if lv is not None else None,
                step=int(r["step"]),
                offset=int(r["_offset"]),
                length=int(r["_length"]),
            )
        )
    return out


def select_entries(
    entries: list[IndexEntry], fields: tuple[Field, ...]
) -> tuple[list[IndexEntry], list[str]]:
    """Mensajes de los campos pedidos y etiquetas de los que no estan en el indice."""
    chosen: list[IndexEntry] = []
    missing: list[str] = []
    for f in fields:
        hits = [e for e in entries if e.matches(f)]
        if hits:
            chosen.extend(hits[:1])
        else:
            missing.append(f.label)
    return chosen, missing


def merge_ranges(entries: list[IndexEntry], max_gap: int = 0) -> list[ByteRange]:
    """Fusiona mensajes contiguos (o separados por <= `max_gap` bytes) en un solo rango."""
    ordered = sorted(entries, key=lambda e: e.offset)
    ranges: list[ByteRange] = []
    for e in ordered:
        if ranges and e.offset - (ranges[-1].end + 1) <= max_gap:
            last = ranges[-1]
            ranges[-1] = ByteRange(last.start, e.offset + e.length - 1, (*last.entries, e))
        else:
            ranges.append(ByteRange(e.offset, e.offset + e.length - 1, (e,)))
    return ranges


def split_range(rng: ByteRange, data: bytes) -> list[tuple[IndexEntry, bytes]]:
    """Reparte los bytes de un rango entre sus mensajes, validando los marcadores GRIB."""
    if len(data) != rng.length:
        raise ValueError(
            f"rango {rng.start}-{rng.end}: esperados {rng.length}, recibidos {len(data)}"
        )
    out: list[tuple[IndexEntry, bytes]] = []
    for e in rng.entries:
        msg = data[e.offset - rng.start : e.offset - rng.start + e.length]
        if msg[:4] != b"GRIB" or msg[-4:] != b"7777":
            raise ValueError(f"mensaje {e.param}@{e.levelist} paso {e.step} no es un GRIB valido")
        out.append((e, msg))
    return out


@dataclass
class TransferStats:
    requests: int = 0
    bytes: int = 0
    n503: int = 0
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, *, requests: int = 0, nbytes: int = 0, n503: int = 0, retries: int = 0) -> None:
        with self._lock:
            self.requests += requests
            self.bytes += nbytes
            self.n503 += n503
            self.retries += retries


class Downloader:
    """Cliente HTTP con reintentos y backoff exponencial ante 503 (`Slow Down`)."""

    def __init__(
        self,
        concurrency: int = 2,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.concurrency = concurrency
        self.base_url = base_url
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(60.0, connect=20.0),
            limits=httpx.Limits(max_connections=max(concurrency, 1) * 2),
        )
        self.stats = TransferStats()
        self._sleep = sleep

    def _request(
        self, method: str, key: str, headers: dict[str, str] | None = None
    ) -> httpx.Response | None:
        """Devuelve la respuesta, o `None` si el objeto no existe (403/404)."""
        url = self.base_url + key
        err: Exception | None = None
        for i in range(MAX_RETRIES):
            try:
                self.stats.add(requests=1)
                r = self.client.request(method, url, headers=headers)
            except httpx.TransportError as e:
                err = e
                self.stats.add(retries=1)
                self._sleep(min(2.0**i, BACKOFF_CAP_S))
                continue
            if r.status_code in (403, 404):
                return None
            if r.status_code in _STATUS_RETRY:
                if r.status_code == 503:  # noqa: PLR2004
                    self.stats.add(n503=1)
                self.stats.add(retries=1)
                err = RuntimeError(f"HTTP {r.status_code}")
                self._sleep(min(2.0**i, BACKOFF_CAP_S))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"fallo {url}: {err}")

    def exists(self, key: str) -> bool:
        return self._request("HEAD", key) is not None

    def read_index(self, key: str) -> list[IndexEntry] | None:
        r = self._request("GET", key)
        if r is None:
            return None
        self.stats.add(nbytes=len(r.content))
        return parse_index(r.text)

    def fetch_range(self, key: str, rng: ByteRange) -> bytes:
        r = self._request("GET", key, {"Range": f"bytes={rng.start}-{rng.end}"})
        if r is None:
            raise FileNotFoundError(key)
        self.stats.add(nbytes=len(r.content))
        return r.content

    def fetch_many(self, jobs: list[tuple[str, ByteRange]]) -> list[bytes]:
        """Descarga rangos (clave, rango) con la concurrencia configurada, en orden."""
        if self.concurrency <= 1:
            return [self.fetch_range(k, r) for k, r in jobs]

        def one(job: tuple[str, ByteRange]) -> bytes:
            return self.fetch_range(job[0], job[1])

        with ThreadPoolExecutor(self.concurrency) as pool:
            return list(pool.map(one, jobs))


@dataclass
class StepDownload:
    step: int
    messages: list[tuple[IndexEntry, bytes]]
    missing: list[str]


def plan_step(
    entries: list[IndexEntry], include_lsm: bool, max_gap: int = 0
) -> tuple[list[ByteRange], list[str]]:
    fields = (*DYNAMIC_FIELDS, LSM) if include_lsm else DYNAMIC_FIELDS
    chosen, missing = select_entries(entries, fields)
    return merge_ranges(chosen, max_gap), missing


__all__ = [
    "ALL_FIELDS",
    "ByteRange",
    "Downloader",
    "IndexEntry",
    "StepDownload",
    "TransferStats",
    "merge_ranges",
    "parse_index",
    "plan_step",
    "select_entries",
    "split_range",
]
