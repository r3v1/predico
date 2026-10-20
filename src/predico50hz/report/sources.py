# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Fuentes de datos, recorrido de los volúmenes, reloj diario y versiones de los datos."""

import datetime as dt
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from predico50hz.report.paths import Paths
from predico50hz.report.quality import latest_ifs_report
from predico50hz.report.util import dir_size, read_json

MADRID = ZoneInfo("Europe/Madrid")
CLOSE_UTC = {"summer": "13:00", "winter": "14:00"}  # cierre a las 15:00 de Madrid


def _hhmm(seconds: float) -> str:
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}"


def _secs(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def nwp_transfer(paths: Paths) -> dict[str, Any]:
    """Volumen descargado de dynamical y tiempo por pasada.

    Solo las lecturas en serie (`init_workers == 1`) son atribuibles a una pasada: con varias a
    la vez el contador de red mezcla pasadas (docs/stage1-dynamical.md). El total es la
    mediana serie por el número de pasadas distintas ingeridas, es decir, una estimación.
    """
    doc = read_json(paths.stage1) or {}
    serial: dict[str, dict[str, Any]] = {}
    ingested: set[str] = set()
    for run in doc.get("runs", []):
        for p in run.get("passes", []):
            if p.get("status") != "ok":
                continue
            ingested.add(p["init_date"])
            if run.get("init_workers") == 1 and p.get("mb_transferred"):
                serial[p["init_date"]] = p
    mbs = [p["mb_transferred"] for p in serial.values()]
    secs = [p["read_s"] + p["bronze_s"] + p["silver_s"] for p in serial.values()]
    return {
        "passes": len(ingested),
        "bytes": statistics.median(mbs) * 1e6 * len(ingested) if mbs else None,
        "pass_seconds": round(statistics.median(secs)) if secs else None,
    }


def last_entsoe_fetch(paths: Paths) -> str | None:
    stamps = [
        d.name.split("=", 1)[1]
        for d in paths.bronze_entsoe.glob("*/fetched_at=*")
        if d.is_dir() and "=" in d.name
    ]
    if not stamps:
        return None
    return dt.datetime.strptime(max(stamps), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC).isoformat()


def volumes(paths: Paths) -> dict[str, Any]:
    t = nwp_transfer(paths)
    mastr = read_json(paths.validation / "mastr.json") or {}
    zip_path = mastr.get("zip")
    zip_bytes = (
        dir_size(paths.mastr_raw / mastr["export_date"]) if mastr.get("export_date") else None
    )
    entsoe_raw = dir_size(paths.bronze_entsoe)
    return {
        "nwp": {
            "download": t["bytes"],
            "bronze": dir_size(paths.nwp_bronze),
            "silver": dir_size(paths.silver / "ifs_ens_clusters"),
            "passes": t["passes"],
        },
        "entsoe": {
            "download": entsoe_raw,
            "bronze": entsoe_raw,
            "silver": dir_size(paths.silver / "entsoe"),
        },
        "mastr": {
            "download": zip_bytes,
            "bronze": zip_bytes,
            "silver": dir_size(paths.silver / "mastr"),
            "zip": zip_path,
        },
    }


def versions(paths: Paths) -> dict[str, Any]:
    ifs = latest_ifs_report(paths) or {}
    mastr = read_json(paths.validation / "mastr.json") or {}
    check = read_json(paths.stage0) or {}
    export = mastr.get("export_date")
    return {
        "bronze_snapshot": ifs.get("bronze_snapshot_id"),
        "dataset_version": (check.get("dataset") or {}).get("dataset_version"),
        "mastr_export": f"{export[:4]}-{export[4:6]}-{export[6:]}" if export else None,
        "entsoe_last_fetch": last_entsoe_fetch(paths),
        "ifs_range": [ifs.get("start"), ifs.get("end")] if ifs else None,
    }


def clock(paths: Paths, today: dt.date) -> dict[str, Any] | None:
    check = read_json(paths.stage0) or {}
    days = (check.get("latency") or {}).get("per_day") or []
    if not days:
        return None
    done = [_secs(d["complete_utc"]) for d in days]
    expand = [_secs(d["expand_utc"]) for d in days]
    noon = dt.datetime.combine(today, dt.time(12), tzinfo=MADRID)
    summer = bool(noon.dst())
    # próximo cambio de hora (comprobación día a día durante un año)
    nxt = next(
        (
            today + dt.timedelta(days=i)
            for i in range(1, 366)
            if bool(
                dt.datetime.combine(today + dt.timedelta(days=i), dt.time(12), tzinfo=MADRID).dst()
            )
            != summer
        ),
        None,
    )
    return {
        "n_days": len(days),
        "first_day": days[0]["init"],
        "last_day": days[-1]["init"],
        "start_min": _hhmm(min(expand)),
        "arrival_min": _hhmm(min(done)),
        "arrival_median": _hhmm(statistics.median(done)),
        "arrival_max": _hhmm(max(done)),
        "arrival_median_s": statistics.median(done),
        "arrival_min_s": min(done),
        "arrival_max_s": max(done),
        "close_summer": CLOSE_UTC["summer"],
        "close_winter": CLOSE_UTC["winter"],
        "summer_now": summer,
        "utc_offset_h": int(noon.utcoffset().total_seconds() // 3600) if noon.utcoffset() else 1,
        "next_change": nxt.isoformat() if nxt else None,
        "pass_seconds": nwp_transfer(paths)["pass_seconds"],
    }
