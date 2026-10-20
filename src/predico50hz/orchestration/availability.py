# pyright: basic
"""Hora real de disponibilidad de la pasada 00Z (F1.5): dynamical.org y ECMWF Open Data `enfo`.

**dynamical**: cada día hay una ráfaga de snapshots de Icechunk, `Expand dataset` (añade el
`init_time` con NaN), ~38 `Update worker N` y un `Update at ...` final (`docs/stage0-dynamical.md`
§3). La pasada está completa en el primer `Update at` posterior al `Expand dataset` de ese día
en el que un chunk del dominio (último paso) ya no es NaN. Su `written_at` es la hora exacta.

**enfo**: la hora exacta es la cabecera `Last-Modified` del `.index` del último paso necesario.

La tabla Delta `nwp_availability` tiene una fila por (día, fuente) y conserva la primera detección.
"""

import datetime as dt
import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
import numpy as np
import polars as pl

from predico50hz.features.timing import closure_utc
from predico50hz.orchestration import locks
from predico50hz.orchestration.settings import OpsSettings

EXPAND_MSG = "Expand dataset"
FINAL_PREFIX = "Update at"
PROBE_VARIABLE = "wind_u_100m"
SOURCE_DYNAMICAL = "dynamical"
SOURCE_ENFO = "ecmwf_enfo"

SCHEMA: dict[str, Any] = {
    "day": pl.Date,
    "source": pl.String,
    "available_at": pl.Datetime("us", "UTC"),
    "detected_at": pl.Datetime("us", "UTC"),
    "source_ref": pl.String,
    "closure_utc": pl.Datetime("us", "UTC"),
    "margin_min": pl.Float64,
    "detail": pl.String,
    "host": pl.String,
    "catch_up": pl.Boolean,  # detectada tarde (tras el límite de espera): no vale para el margen
}


@dataclass(frozen=True)
class SnapshotInfo:
    id: str
    message: str
    written_at: dt.datetime


class SnapshotSource(Protocol):
    def snapshots_since(self, since: dt.datetime) -> list[SnapshotInfo]:
        """Snapshots de `main` escritos desde `since`, del más antiguo al más reciente."""
        ...

    def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
        """¿La pasada 00Z de `day` está completa (sin NaN en el chunk sonda) en ese snapshot?"""
        ...


@dataclass(frozen=True)
class Detection:
    day: dt.date
    available_at: dt.datetime
    snapshot_id: str
    message: str


def find_complete_snapshot(src: SnapshotSource, day: dt.date) -> Detection | None:
    """Primer `Update at` tras el `Expand dataset` del día cuyo chunk sonda está completo."""
    since = dt.datetime.combine(day, dt.time(0), tzinfo=dt.UTC)
    snaps = src.snapshots_since(since)
    expand = next((i for i, s in enumerate(snaps) if s.message == EXPAND_MSG), None)
    if expand is None:
        return None
    for s in snaps[expand + 1 :]:
        if s.message.startswith(FINAL_PREFIX) and src.is_complete(s.id, day):
            return Detection(day, s.written_at, s.id, s.message)
    return None


class CachedSnapshots:
    """Lee la historia de snapshots una sola vez (la puesta al día consulta varios días)."""

    def __init__(self, inner: SnapshotSource, since: dt.datetime) -> None:
        self.inner = inner
        self._snaps = inner.snapshots_since(since)
        self._since = since

    def snapshots_since(self, since: dt.datetime) -> list[SnapshotInfo]:
        if since < self._since:  # pide más atrás de lo guardado: se amplía
            self._snaps, self._since = self.inner.snapshots_since(since), since
        return [s for s in self._snaps if s.written_at >= since]

    def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
        return self.inner.is_complete(snapshot_id, day)


class IcechunkDynamical:
    """Lector real de los snapshots de dynamical.org (solo metadatos y un chunk por sondeo)."""

    def __init__(self, href: str | None = None) -> None:
        import icechunk  # noqa: PLC0415

        from predico50hz.ingest.dynamical.source import resolve_href  # noqa: PLC0415

        self.href = href or resolve_href()
        self.repo = icechunk.Repository.open(icechunk.http_storage(self.href))

    def snapshots_since(self, since: dt.datetime) -> list[SnapshotInfo]:
        out: list[SnapshotInfo] = []
        for s in self.repo.ancestry(branch="main"):  # del más reciente al más antiguo
            if s.written_at < since:
                break
            out.append(SnapshotInfo(s.id, s.message, s.written_at.astimezone(dt.UTC)))
        return out[::-1]

    def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
        import xarray as xr  # noqa: PLC0415

        from predico50hz.ingest.dynamical.config import DOMAIN  # noqa: PLC0415
        from predico50hz.ingest.dynamical.source import axis_slice, init_index  # noqa: PLC0415

        ds = xr.open_zarr(self.repo.readonly_session(snapshot_id=snapshot_id).store, chunks=None)
        try:
            i = init_index(ds["init_time"].values, np.datetime64(day.isoformat(), "ns"))
        except KeyError:
            return False
        ys = axis_slice(ds["latitude"].values, DOMAIN.lat_min, DOMAIN.lat_max)
        xs = axis_slice(ds["longitude"].values, DOMAIN.lon_min, DOMAIN.lon_max)
        # último paso de la pasada (360 h) en una esquina del dominio: un solo chunk
        arr = ds[PROBE_VARIABLE].isel(
            init_time=i,
            lead_time=-1,
            latitude=slice(ys.start, ys.start + 2),
            longitude=slice(xs.start, xs.start + 2),
        )
        return not bool(np.isnan(arr.values).any())


# -- respaldo enfo --------------------------------------------------------------------------


def enfo_index_url(base: str, day: dt.date, step_h: int) -> str:
    stem = f"{day:%Y%m%d}000000-{step_h}h-enfo-ef"
    return f"{base}{day:%Y%m%d}/00z/ifs/0p25/enfo/{stem}.index"


def probe_enfo(settings: OpsSettings, day: dt.date) -> dt.datetime | None:
    """`Last-Modified` del `.index` de `enfo` (hora exacta de subida) o `None` si aún no existe."""
    url = enfo_index_url(settings.enfo_base_url, day, settings.enfo_step_h)
    r = httpx.head(url, timeout=settings.http_timeout_s, follow_redirects=True)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    lm = r.headers.get("last-modified")
    return parsedate_to_datetime(lm).astimezone(dt.UTC) if lm else None


# -- tabla Delta ----------------------------------------------------------------------------


def read_availability(settings: OpsSettings) -> pl.DataFrame:
    path = settings.availability_path
    if not (path / "_delta_log").exists():
        return pl.DataFrame(schema=SCHEMA)
    df = pl.read_delta(str(path))
    # tablas anteriores a las columnas `host` y `catch_up`
    if "host" not in df.columns:
        df = df.with_columns(host=pl.lit(settings.host))
    if "catch_up" not in df.columns:
        df = df.with_columns(catch_up=pl.lit(False))
    return df.select(list(SCHEMA)).cast(pl.Schema(SCHEMA))


def record_availability(  # noqa: PLR0913, PLR0917
    settings: OpsSettings,
    day: dt.date,
    source: str,
    available_at: dt.datetime,
    source_ref: str,
    detail: dict[str, Any],
    detected_at: dt.datetime,
    catch_up: bool = False,
) -> bool:
    """Inserta la fila (día, fuente) si no existe; si existe se conserva la primera detección.

    Devuelve `True` si escribió una fila nueva.
    """
    with locks.file_lock(settings.lock_path, locks.OPS, settings.lock_timeout_s):
        cur = read_availability(settings)
        if cur.filter((pl.col("day") == day) & (pl.col("source") == source)).height:
            return False
        closure = closure_utc(day)
        row = pl.DataFrame(
            {
                "day": [day],
                "source": [source],
                "available_at": [available_at],
                "detected_at": [detected_at],
                "source_ref": [source_ref],
                "closure_utc": [closure],
                "margin_min": [(closure - available_at).total_seconds() / 60],
                "detail": [json.dumps(detail, ensure_ascii=False, default=str)],
                "host": [settings.host],
                "catch_up": [catch_up],
            },
            schema=SCHEMA,
        )
        settings.availability_path.parent.mkdir(parents=True, exist_ok=True)
        path = str(settings.availability_path)
        has_log = (settings.availability_path / "_delta_log").exists()
        if not has_log or "catch_up" in pl.scan_delta(path).collect_schema().names():
            row.write_delta(path, mode="append")
        else:  # tabla anterior a `host`/`catch_up`: se reescribe migrada
            pl.concat([cur, row]).write_delta(
                path, mode="overwrite", delta_write_options={"schema_mode": "overwrite"}
            )
    return True


# -- sondeo ---------------------------------------------------------------------------------


@dataclass
class PollResult:
    detection: Detection | None
    detected_at: dt.datetime | None
    polls: int = 0
    enfo_available_at: dt.datetime | None = None
    enfo_detected_at: dt.datetime | None = None
    notes: list[str] = field(default_factory=lambda: [])


def poll_until_available(  # noqa: PLR0913
    settings: OpsSettings,
    day: dt.date,
    src: SnapshotSource,
    *,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    sleep: Callable[[float], None] = time.sleep,
    enfo_probe: Callable[[dt.date], dt.datetime | None] | None = None,
) -> PollResult:
    """Sondea cada `poll_interval_s` hasta la pasada completa o `poll_deadline_utc` de `day`.

    Siempre hace al menos un intento (una ejecución tardía tras un arranque en frío no se
    descarta). El sondeo de `enfo` es oportunista: un fallo de red solo deja una nota.
    """
    deadline = dt.datetime.combine(day, settings.poll_deadline_utc, tzinfo=dt.UTC)
    probe = enfo_probe or (lambda d: probe_enfo(settings, d))
    res = PollResult(None, None)
    for attempt in itertools.count(1):
        res.polls = attempt
        t = now()
        if res.enfo_available_at is None:
            try:
                if (lm := probe(day)) is not None:
                    res.enfo_available_at, res.enfo_detected_at = lm, t
            except (httpx.HTTPError, ValueError) as e:
                res.notes.append(f"enfo: {type(e).__name__}")
        det = find_complete_snapshot(src, day)
        if det is not None:
            res.detection, res.detected_at = det, t
            return res
        remaining = (deadline - t).total_seconds()
        if remaining <= 0:
            return res
        sleep(min(settings.poll_interval_s, remaining))
    raise AssertionError  # pragma: no cover
