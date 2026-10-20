"""Hora de primera publicación del precio day-ahead y del pronóstico del operador (F2.5, F3 §9).

Se mide con el bronce de ENTSO-E: para cada día de cierre D, el objetivo es el día local D+1 y se
busca la primera descarga (`fetched_at=`) en la que ese día aparece completo. **La resolución es la
de las descargas (horaria) y el resultado es una cota superior**: la publicación real fue en algún
momento entre la última descarga incompleta (`last_incomplete_at`) y la primera completa.

Un día solo cuenta como medido si hubo antes una descarga que lo observó incompleto; si no, la
medida está censurada por la izquierda (p. ej. el histórico bajado de golpe) y se excluye.
"""

import datetime as dt
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from predico50hz.features.timing import closure_utc, target_grid
from predico50hz.ingest.entsoe.bronze import list_chunks, read_chunk
from predico50hz.ingest.entsoe.datasets import WIND_OFFSHORE, WIND_ONSHORE
from predico50hz.ingest.entsoe.parse import parse_raw

QUARTER = dt.timedelta(minutes=15)
MAX_GAP_MIN = 90  # si entre la última descarga incompleta y la primera completa pasa más, es burda
DATASETS = ("prices", "forecast")
WARNING = (
    "La resolución es la de las descargas (horaria): la hora medida es una COTA SUPERIOR de la "
    "publicación real, que ocurrió entre last_incomplete_at y first_complete_at. Los días sin "
    "una descarga previa que los viera incompletos están censurados, y los que tienen más de "
    f"{MAX_GAP_MIN} min entre ambas descargas son burdos: ninguno entra en el resumen."
)


@dataclass(frozen=True)
class Fetch:
    fetched_at: dt.datetime
    directory: Path
    start: dt.date | None  # ventana pedida (de `_run.json`), si consta
    end: dt.date | None


def list_fetches(bronze: Path, dataset: str) -> list[Fetch]:
    out: list[Fetch] = []
    seen: dict[dt.datetime, Path] = {}
    for c in list_chunks(bronze, dataset):
        seen.setdefault(c.fetched_at, c.path.parent)
    for fetched, d in sorted(seen.items()):
        start = end = None
        info = d / "_run.json"
        if info.exists():
            try:
                j = json.loads(info.read_text())
                start, end = dt.date.fromisoformat(j["start"]), dt.date.fromisoformat(j["end"])
            except ValueError, KeyError, TypeError:
                pass
        out.append(Fetch(fetched, d, start, end))
    return out


def _quarters(df: pl.DataFrame) -> set[dt.datetime]:
    """Inicios de cuarto de hora cubiertos (una fila horaria cubre cuatro)."""
    out: set[dt.datetime] = set()
    for ts, res in df.select("ts_utc", "resolution_min").iter_rows():
        n = max(1, (res or 15) // 15)
        out.update(ts + QUARTER * k for k in range(n))
    return out


def fetch_covers(f: Fetch, grid: list[dt.datetime]) -> bool:
    """¿La ventana pedida en esa descarga incluía el día objetivo?"""
    if f.start is None or f.end is None:
        return True
    return f.start <= grid[0].date() and f.end >= grid[-1].date()


def is_complete(bronze: Path, dataset: str, f: Fetch, grid: list[dt.datetime]) -> bool:
    """¿Esa descarga contiene los 96/92/100 cuartos (precio) o B18 y B19 completos (pronóstico)?"""
    want = set(grid)
    need = {WIND_OFFSHORE, WIND_ONSHORE} if dataset == "forecast" else None
    frames: list[pl.DataFrame] = []
    for c in list_chunks(bronze, dataset):
        if c.fetched_at != f.fetched_at or c.nodata:
            continue
        df = parse_raw(read_chunk(c)).filter(
            pl.col("value").is_not_null() & pl.col("ts_utc").is_between(grid[0], grid[-1])
        )
        if not df.is_empty():
            frames.append(df)
    if not frames:
        return False
    df = pl.concat(frames)
    if need is None:
        return want <= _quarters(df)
    return all(want <= _quarters(df.filter(pl.col("psr_type") == psr)) for psr in need)


def measure_day(
    bronze: Path, dataset: str, d: dt.date, fetches: list[Fetch], now: dt.datetime
) -> dict[str, Any]:
    """Resultado para el cierre de `d` (objetivo: día local d+1)."""
    grid = target_grid(d)
    close = closure_utc(d)
    lo = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(0), tzinfo=dt.UTC)
    hi = dt.datetime.combine(d + dt.timedelta(days=2), dt.time(0), tzinfo=dt.UTC)
    window = [f for f in fetches if lo <= f.fetched_at < hi and fetch_covers(f, grid)]
    res: dict[str, Any] = {
        "closure_day": d.isoformat(),
        "target_day": (grid[0] + dt.timedelta(hours=12)).date().isoformat(),
        "closure_utc": close.isoformat(timespec="minutes"),
        "n_fetches": len(window),
        "first_complete_at": None,
        "last_incomplete_at": None,
        "margin_min": None,
        "before_closure": None,
    }
    last_incomplete: Fetch | None = None
    for f in window:
        if is_complete(bronze, dataset, f, grid):
            if last_incomplete is None:
                res["status"] = "censored"  # ya estaba al empezar a observarlo
                res["first_seen_complete_at"] = f.fetched_at.isoformat(timespec="seconds")
                return res
            gap = (f.fetched_at - last_incomplete.fetched_at).total_seconds() / 60
            res |= {
                "status": "measured" if gap <= MAX_GAP_MIN else "coarse",
                "gap_min": round(gap, 1),
                "first_complete_at": f.fetched_at.isoformat(timespec="seconds"),
                "last_incomplete_at": last_incomplete.fetched_at.isoformat(timespec="seconds"),
                "margin_min": round((close - f.fetched_at).total_seconds() / 60, 1),
                "before_closure": f.fetched_at <= close,
                "minutes_after_midnight_utc": round(
                    (
                        f.fetched_at - dt.datetime.combine(d, dt.time(0), tzinfo=dt.UTC)
                    ).total_seconds()
                    / 60
                ),
            }
            return res
        last_incomplete = f
    res["status"] = "not_published" if window and now > close else "pending"
    if not window:
        res["status"] = "no_fetches"
    return res


DAY_MIN = 1440


def _hhmm(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60 % 24:02d}:{m % 60:02d}" + (f" (+{m // DAY_MIN} d)" if m >= DAY_MIN else "")


def summarize(days: list[dict[str, Any]]) -> dict[str, Any]:
    done = [x for x in days if x["status"] == "measured"]
    mins = [x["minutes_after_midnight_utc"] for x in done]
    out: dict[str, Any] = {
        "n_days_listed": len(days),
        "n_measured": len(done),
        "n_censored": sum(x["status"] == "censored" for x in days),
        "n_coarse": sum(x["status"] == "coarse" for x in days),
        "n_not_published_by_closure": sum(x["status"] == "not_published" for x in days),
    }
    if done:
        out |= {
            "utc_time_min": _hhmm(min(mins)),
            "utc_time_median": _hhmm(statistics.median(mins)),
            "utc_time_max": _hhmm(max(mins)),
            "pct_before_closure": round(
                100 * sum(x["before_closure"] for x in done) / len(done), 1
            ),
            "margin_min_min": min(x["margin_min"] for x in done),
            "margin_min_median": statistics.median(x["margin_min"] for x in done),
        }
    return out


def compute(
    bronze: Path, since: dt.date, now: dt.datetime, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Medida de todos los cierres desde `since` hasta hoy. Reutiliza los días ya medidos."""
    today = now.date()
    result: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "warning": WARNING,
        "datasets": {},
    }
    for dataset in DATASETS:
        fetches = list_fetches(bronze, dataset)
        cached = {
            x["closure_day"]: x
            for x in ((previous or {}).get("datasets", {}).get(dataset, {}).get("days", []))
            if x.get("status") == "measured"
        }
        days: list[dict[str, Any]] = []
        d = since
        while d <= today:
            hit = cached.get(d.isoformat())
            days.append(hit or measure_day(bronze, dataset, d, fetches, now))
            d += dt.timedelta(days=1)
        # los días sin ninguna descarga no aportan nada al listado
        days = [x for x in days if x["status"] != "no_fetches"]
        result["datasets"][dataset] = {"summary": summarize(days), "days": days}
    return result


def render(result: dict[str, Any]) -> str:
    lines = ["Hora de primera publicación (UTC) respecto al cierre de Predico", result["warning"]]
    for name, block in result["datasets"].items():
        s = block["summary"]
        lines.append(
            f"\n[{name}] medidos={s['n_measured']} censurados={s['n_censored']} "
            f"burdos={s['n_coarse']} sin publicar al cierre={s['n_not_published_by_closure']}"
        )
        if s["n_measured"]:
            lines.append(
                f"  hora UTC mín/mediana/máx: {s['utc_time_min']} / {s['utc_time_median']} / "
                f"{s['utc_time_max']}   antes del cierre: {s['pct_before_closure']} %"
            )
        for x in block["days"]:
            lines.append(
                f"  cierre {x['closure_day']} -> objetivo {x['target_day']}: {x['status']}"
                + (
                    f"  primera={x['first_complete_at']} margen={x['margin_min']} min"
                    f" (ventana {x['gap_min']} min)"
                    if x["status"] in ("measured", "coarse")
                    else ""
                )
            )
    return "\n".join(lines)
