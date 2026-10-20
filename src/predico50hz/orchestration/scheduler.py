"""Planificador mínimo para Docker (`serve`): lanza los jobs a sus horas UTC, sin cron externo.

Cada job corre en un subproceso (`python -m predico50hz.orchestration <job>`), así que el
sondeo de horas de `nwp-daily` no bloquea a los demás; los locks serializan las escrituras.
Al arrancar, los jobs diarios cuya hora de hoy ya pasó y que no constan en `runs.jsonl` se
lanzan enseguida (equivale a `Persistent=true` de systemd).
"""

import datetime as dt
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from predico50hz.orchestration.runlog import read_records
from predico50hz.orchestration.settings import OpsSettings


@dataclass(frozen=True)
class Schedule:
    job: str
    hour: int | None  # None = cada hora
    minute: int

    def due(self, t: dt.datetime) -> bool:
        return t.minute == self.minute and (self.hour is None or t.hour == self.hour)


SCHEDULES = (
    Schedule("entsoe-daily", 6, 0),
    Schedule("nwp-daily", 7, 45),
    Schedule("entsoe-hourly", None, 0),
)


def due_jobs(t: dt.datetime) -> list[str]:
    return [s.job for s in SCHEDULES if s.due(t)]


def catchup_jobs(t: dt.datetime, runs: list[dict[str, Any]]) -> list[str]:
    """Jobs diarios de hoy cuya hora ya pasó y sin ninguna ejecución registrada hoy."""
    out: list[str] = []
    for s in SCHEDULES:
        if s.hour is None or (t.hour, t.minute) < (s.hour, s.minute):
            continue
        if not any(r.get("job") == s.job and r.get("day") == t.date().isoformat() for r in runs):
            out.append(s.job)
    return out


def serve(
    settings: OpsSettings,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    launch: Callable[[str], object] | None = None,
    max_ticks: int | None = None,
) -> None:
    def default_launch(job: str) -> object:
        print(f"{now().isoformat(timespec='seconds')} lanzando {job}", flush=True)
        return subprocess.Popen([sys.executable, "-m", "predico50hz.orchestration", job])

    go = launch or default_launch
    children: list[Any] = []
    start = now()
    for job in catchup_jobs(start, read_records(settings.run_log_path)):
        children.append(go(job))
    last_minute = start.replace(second=0, microsecond=0)
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        t = now().replace(second=0, microsecond=0)
        if t > last_minute:
            # recorre los minutos transcurridos por si el bucle se retrasó
            m = last_minute + dt.timedelta(minutes=1)
            while m <= t:
                children += [go(j) for j in due_jobs(m)]
                m += dt.timedelta(minutes=1)
            last_minute = t
        children = [c for c in children if getattr(c, "poll", lambda: 0)() is None]
        time.sleep(5 if max_ticks is None else 0)
