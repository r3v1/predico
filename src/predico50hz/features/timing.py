"""Tiempos de la tarea: cierre, día objetivo y malla de 15 min, con el cambio de hora de Berlín.

Todas las funciones devuelven instantes UTC con zona (`datetime.UTC`). `D` es la fecha local
del cierre (`forecast_issue_date`); el día objetivo es el día local D+1.
"""

import datetime as dt

from predico50hz.features.config import (
    BERLIN,
    CALENDAR_LATENCY,
    CAPACITY_LATENCY,
    CLOSURE_LOCAL,
    NWP_INIT_HOUR,
    NWP_LATENCY,
    STEP,
    UTC,
)


def _local_midnight_utc(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(0, 0), tzinfo=BERLIN).astimezone(UTC)


def closure_utc(d: dt.date) -> dt.datetime:
    """Cierre de Predico de `d` en UTC: 15:00 de Berlín (13:00 en verano, 14:00 en invierno)."""
    return dt.datetime.combine(d, CLOSURE_LOCAL, tzinfo=BERLIN).astimezone(UTC)


def target_window(d: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Ventana UTC `[inicio, fin)` del día local D+1 (23:00-23:00 invierno, 22:00-22:00 verano)."""
    one = dt.timedelta(days=1)
    return _local_midnight_utc(d + one), _local_midnight_utc(d + 2 * one)


def target_grid(d: dt.date) -> list[dt.datetime]:
    """Inicios `ts_utc` de los intervalos de 15 min del día objetivo: 96, 92 (verano) o 100."""
    start, end = target_window(d)
    n = (end - start) // STEP
    return [start + i * STEP for i in range(n)]


def nwp_init_time(d: dt.date) -> dt.datetime:
    """Pasada NWP usada para `d`: la 00Z de `d` (UTC)."""
    return dt.datetime.combine(d, dt.time(NWP_INIT_HOUR), tzinfo=UTC)


def available_at_nwp(d: dt.date) -> dt.datetime:
    """Hora UTC a partir de la cual se considera disponible la pasada de `d` (00Z + latencia)."""
    return nwp_init_time(d) + NWP_LATENCY


def available_at_capacity(d: dt.date) -> dt.datetime:
    """Capacidad MaStR de la fecha `d`: disponible desde las 00:00 UTC de `d`."""
    return dt.datetime.combine(d, dt.time(0, 0), tzinfo=UTC) + CAPACITY_LATENCY


def available_at_calendar(d: dt.date) -> dt.datetime:
    """Calendario: determinista, disponible desde las 00:00 UTC de `d`."""
    return dt.datetime.combine(d, dt.time(0, 0), tzinfo=UTC) + CALENDAR_LATENCY
