"""Registro de ejecuciones (`runs.jsonl`): una línea JSON por ejecución de un job.

Nunca se escribe el token de ENTSO-E: todo texto de error pasa por `redact`.
"""

import datetime as dt
import json
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from predico50hz.ingest.entsoe.redact import redact
from predico50hz.orchestration import locks
from predico50hz.orchestration.settings import OpsSettings

MAX_ERROR_CHARS = 600
FAILED = frozenset({"error", "unit_failed"})


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def summarize_error(e: BaseException | str) -> str:
    text = e if isinstance(e, str) else f"{type(e).__name__}: {e}"
    return redact(" ".join(text.split()))[:MAX_ERROR_CHARS]


@dataclass
class Phase:
    name: str
    status: str = "ok"
    duration_s: float = 0.0
    detail: dict[str, Any] = field(default_factory=lambda: {})


@dataclass
class RunRecord:
    run_id: str
    job: str
    day: str
    started_at: str
    ended_at: str
    duration_s: float
    status: str  # ok | error | unit_failed
    error: str | None
    phases: list[Phase]
    details: dict[str, Any]
    source: str = "job"  # job | onfailure
    host: str = ""


def append_record(settings: OpsSettings, rec: RunRecord) -> None:
    rec.host = rec.host or settings.host
    line = json.dumps(asdict(rec), ensure_ascii=False, default=str)
    path = settings.run_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        locks.file_lock(settings.lock_path, locks.OPS, settings.lock_timeout_s),
        path.open("a", encoding="utf-8") as f,
    ):
        f.write(line + "\n")
        f.flush()


def read_records(path: Path) -> list[dict[str, Any]]:
    """Registros válidos de `runs.jsonl` en orden; una línea corrupta se ignora."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)  # pyright: ignore[reportUnknownArgumentType]
    return out


class Run:
    """Contexto de un job: mide fases y escribe el registro aunque el job falle."""

    def __init__(self, settings: OpsSettings, job: str, day: dt.date) -> None:
        self.settings, self.job, self.day = settings, job, day
        self.run_id = uuid.uuid4().hex[:12]
        self.phases: list[Phase] = []
        self.details: dict[str, Any] = {}
        self.record: RunRecord | None = None
        self._t0 = 0.0
        self._started = utcnow()

    def __enter__(self) -> Run:
        self._t0 = time.perf_counter()
        self._started = utcnow()
        return self

    @contextmanager
    def phase(self, name: str) -> Generator[Phase]:
        ph = Phase(name)
        self.phases.append(ph)
        t = time.perf_counter()
        try:
            yield ph
        except BaseException as e:
            ph.status = "error"
            ph.detail["error"] = summarize_error(e)
            raise
        finally:
            ph.duration_s = round(time.perf_counter() - t, 2)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.record = RunRecord(
            run_id=self.run_id,
            job=self.job,
            day=self.day.isoformat(),
            started_at=self._started.isoformat(timespec="seconds"),
            ended_at=utcnow().isoformat(timespec="seconds"),
            duration_s=round(time.perf_counter() - self._t0, 2),
            status="error" if exc is not None else "ok",
            error=summarize_error(exc) if exc is not None else None,
            phases=self.phases,
            details=_clean(self.details),
        )
        append_record(self.settings, self.record)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Pasa los detalles por JSON y redacta cualquier cadena."""
    return json.loads(redact(json.dumps(d, default=str, ensure_ascii=False)))


def record_unit_failure(settings: OpsSettings, unit: str, now: dt.datetime | None = None) -> None:
    """Constancia de que una unidad systemd falló (la escribe el servicio `OnFailure=`)."""
    now = now or utcnow()
    job = unit.removesuffix(".service").removeprefix("predico-")
    ts = now.isoformat(timespec="seconds")
    append_record(
        settings,
        RunRecord(
            run_id=uuid.uuid4().hex[:12],
            job=job,
            day=now.date().isoformat(),
            started_at=ts,
            ended_at=ts,
            duration_s=0.0,
            status="unit_failed",
            error=f"la unidad {unit} terminó con fallo (ver journalctl --user -u {unit})",
            phases=[],
            details={},
            source="onfailure",
        ),
    )


MAX_LOG_BYTES = 10_000_000


class _LogTee:
    """Copia lo que se escribe en un flujo a `ops.log` (con hora y redactado) y lo deja pasar."""

    def __init__(self, stream: Any, log: Any, job: str) -> None:
        self.stream, self.log, self.job, self._buf = stream, log, job, ""

    def write(self, s: str) -> int:
        self.stream.write(s)
        self._buf += s
        *lines, self._buf = self._buf.split("\n")
        stamp = utcnow().isoformat(timespec="seconds")
        for line in lines:
            if line.strip():
                self.log.write(f"{stamp} [{self.job}] {redact(line)}\n")
        self.log.flush()
        return len(s)

    def flush(self) -> None:
        self.stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


@contextmanager
def tee_to_log(settings: OpsSettings, job: str) -> Generator[None]:
    """Durante el job, stdout y stderr también van a `ops.log` (se rota a `.1` a los 10 MB)."""
    import sys  # noqa: PLC0415

    path = settings.log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_name(path.name + ".1"))
    out, err = sys.stdout, sys.stderr
    with path.open("a", encoding="utf-8") as log:
        sys.stdout, sys.stderr = _LogTee(out, log, job), _LogTee(err, log, job)  # type: ignore[assignment]
        try:
            yield
        finally:
            sys.stdout, sys.stderr = out, err
