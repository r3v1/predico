"""Estado de la operación: últimos días por job y puerta G0 (PLAN §1).

**G0** = `G0_DAYS` días UTC seguidos, hasta hoy, en los que `nwp-daily` y `entsoe-daily` tienen al
menos una ejecución correcta y **ninguna** fallida (ni del propio job ni de `OnFailure`). Un día con
fallo no cuenta aunque un reintento manual lo repare: queda como `recovered` y rompe la racha.
Hoy sin ejecución todavía es `pending` (no rompe la racha); un día pasado sin ejecución es
`missing`.
"""

import datetime as dt
from typing import Any

import polars as pl

from predico50hz.orchestration.availability import SOURCE_DYNAMICAL, SOURCE_ENFO, read_availability
from predico50hz.orchestration.runlog import FAILED, read_records
from predico50hz.orchestration.settings import OpsSettings

G0_JOBS = ("nwp-daily", "entsoe-daily")
G0_DAYS = 7
MAX_LOOKBACK_DAYS = 3650
JOBS = ("nwp-daily", "entsoe-daily", "entsoe-hourly", "publication-times")


def day_state(
    runs: list[dict[str, Any]],
    job: str,
    day: dt.date,
    today: dt.date,
    missing_is_failure: bool = True,
) -> str:
    """`ok` | `recovered` | `failed` | `missing` | `pending` | `n/a` para un job y un día.

    `n/a`: anterior a la primera ejecución registrada (antes de instalar la operación).
    `off`: día pasado sin ejecución en un equipo que se apaga (`missing_is_failure=False`); en un
    servidor 24/7 ese mismo día es `missing`, un fallo.
    """
    if runs and day.isoformat() < min(str(r.get("day", "9")) for r in runs):
        return "n/a"
    mine = [r for r in runs if r.get("job") == job and r.get("day") == day.isoformat()]
    n_ok = sum(r.get("status") == "ok" for r in mine)
    n_bad = sum(r.get("status") in FAILED for r in mine)
    if n_bad and n_ok:
        return "recovered"
    if n_bad:
        return "failed"
    if n_ok:
        return "ok"
    if day >= today:
        return "pending"
    return "missing" if missing_is_failure else "off"


def g0(
    runs: list[dict[str, Any]],
    today: dt.date,
    needed: int = G0_DAYS,
    missing_is_failure: bool = True,
) -> dict[str, Any]:
    """Racha de días limpios que termina hoy (o ayer si hoy aún está pendiente)."""
    streak = 0
    day = today
    first = True
    while True:
        states = [day_state(runs, j, day, today, missing_is_failure) for j in G0_JOBS]
        if all(s == "ok" for s in states):
            streak += 1
        elif first and all(s in ("ok", "pending") for s in states):
            pass  # hoy todavía no ha corrido todo: ni suma ni rompe
        elif all(s == "off" for s in states):
            pass  # equipo apagado: ni suma ni rompe (solo si `missing_is_failure` es falso)
        else:
            break
        first = False
        day -= dt.timedelta(days=1)
        if streak >= needed or (today - day).days > MAX_LOOKBACK_DAYS:
            break
    return {"streak_days": streak, "needed": needed, "passed": streak >= needed}


def build(settings: OpsSettings, days: int, now: dt.datetime) -> dict[str, Any]:
    today = now.date()
    runs = read_records(settings.run_log_path)
    avail = read_availability(settings)
    rows: list[dict[str, Any]] = []
    for k in range(days):
        d = today - dt.timedelta(days=k)
        row: dict[str, Any] = {"day": d.isoformat()}
        for j in JOBS:
            row[j] = day_state(runs, j, d, today, settings.missing_is_failure)
        for src in (SOURCE_DYNAMICAL, SOURCE_ENFO):
            hit = avail.filter((pl.col("day") == d) & (pl.col("source") == src))
            if hit.height:
                r = hit.row(0, named=True)
                row[src] = {
                    "available_at": r["available_at"].isoformat(timespec="seconds"),
                    "detected_at": r["detected_at"].isoformat(timespec="seconds"),
                    "margin_min": round(r["margin_min"], 1),
                    "catch_up": bool(r["catch_up"]),
                    "host": r["host"],
                }
            else:
                row[src] = None
        rows.append(row)
    for r in rows:
        r.pop("publication-times")
    hourly = [x for x in runs if x.get("job") == "entsoe-hourly"]
    last_failures = [
        {k: x.get(k) for k in ("job", "day", "started_at", "status", "error")}
        for x in runs
        if x.get("status") in FAILED
    ][-5:]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "host": settings.host,
        "missing_is_failure": settings.missing_is_failure,
        "g0": g0(runs, today, missing_is_failure=settings.missing_is_failure),
        "days": rows,
        "entsoe_hourly": {
            "runs_last_24h": sum(
                dt.datetime.fromisoformat(x["started_at"]) >= now - dt.timedelta(hours=24)
                for x in hourly
            ),
            "failed_last_24h": sum(
                x.get("status") in FAILED
                and dt.datetime.fromisoformat(x["started_at"]) >= now - dt.timedelta(hours=24)
                for x in hourly
            ),
        },
        "last_failures": last_failures,
    }


def _margin(row: dict[str, Any] | None) -> str:
    """Margen operativo; una pasada recuperada tarde no lo tiene (se marca `tarde`)."""
    if not row:
        return "-"
    return "tarde" if row.get("catch_up") else str(row["margin_min"])


def _hm(iso: str) -> str:
    return iso[11:16]


def render(rep: dict[str, Any]) -> str:
    g = rep["g0"]
    lines = [
        f"Estado de la operación a {rep['generated_at']} en {rep['host']} "
        f"(un día sin ejecución {'es fallo' if rep['missing_is_failure'] else 'es off, no fallo'})",
        f"G0 (7 días seguidos sin fallos en nwp-daily y entsoe-daily): "
        f"{'CUMPLIDA' if g['passed'] else 'pendiente'}  racha={g['streak_days']}/{g['needed']}",
        "",
        f"{'día':<11}{'nwp-daily':<11}{'entsoe-d.':<11}{'hourly':<9}"
        f"{'dynamical UTC':<15}{'margen min':<12}{'enfo UTC':<10}",
    ]
    for r in rep["days"]:
        dyn, enfo = r["dynamical"], r["ecmwf_enfo"]
        lines.append(
            f"{r['day']:<11}{r['nwp-daily']:<11}{r['entsoe-daily']:<11}{r['entsoe-hourly']:<9}"
            f"{(_hm(dyn['available_at']) if dyn else '-'):<15}"
            f"{_margin(dyn):<12}"
            f"{(_hm(enfo['available_at']) if enfo else '-'):<10}"
        )
    h = rep["entsoe_hourly"]
    lines.append(
        f"\nentsoe-hourly en 24 h: {h['runs_last_24h']} ejecuciones, "
        f"{h['failed_last_24h']} con fallo"
    )
    if rep["last_failures"]:
        lines.append("Últimos fallos:")
        lines += [
            f"  {f['started_at']} {f['job']} [{f['status']}] {f['error']}"
            for f in rep["last_failures"]
        ]
    return "\n".join(lines)
