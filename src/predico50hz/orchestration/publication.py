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
from predico50hz.ingest.entsoe.bronze import Chunk, list_chunks, read_chunk
from predico50hz.ingest.entsoe.client import EntsoeClient, NoDataError
from predico50hz.ingest.entsoe.datasets import DATASETS as ENTSOE_DATASETS
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
    bronze: Path,
    since: dt.date,
    now: dt.datetime,
    previous: dict[str, Any] | None = None,
    observations: Path | None = None,
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
    if observations is not None:
        merged = merge_observations(
            read_observations(observations), observations_from_bronze(bronze, since)
        )
        result["generation_lag"] = lag_report(merged, since, now)
    return result


# -- retraso de la generación real publicada (issue #11) -----------------------------------------

LAG_WINDOW = dt.timedelta(hours=36)  # ventana de la consulta ligera
LAG_STALE_MIN = 90  # una observación más vieja que esto antes del cierre no representa el cierre
LAG_WARNING = (
    "Generación real B18+B19 (A75/A16, 50Hertz). `last_step` es el INICIO del último cuarto de "
    "hora con dato en ambos tipos (el menor de los dos). El retraso es `observado_en - last_step`; "
    "por cierre se toma la última observación anterior al cierre y se mide `cierre - last_step`."
)


def last_published_step(raws: list[bytes]) -> dict[str, Any]:
    """Último cuarto con valor en B18 y B19 de respuestas A75; `last_step` es el menor."""
    df = pl.concat([parse_raw(r) for r in raws]).filter(
        pl.col("value").is_not_null() & ~pl.col("implied")
    )
    last: dict[str, dt.datetime | None] = {}
    for psr in (WIND_OFFSHORE, WIND_ONSHORE):
        sub = df.filter(pl.col("psr_type") == psr)
        if sub.is_empty():
            last[psr] = None
            continue
        row = sub.sort("ts_utc").tail(1).row(0, named=True)
        # una fila horaria cubre cuatro cuartos: el último empieza 45 min después
        last[psr] = row["ts_utc"] + QUARTER * (max(1, (row["resolution_min"] or 15) // 15) - 1)
    steps = [v for v in last.values() if v is not None]
    both = len(steps) == len(last)
    return {
        "last_step": min(steps).isoformat(timespec="minutes") if both else None,
        "last_b18": _iso(last[WIND_OFFSHORE]),
        "last_b19": _iso(last[WIND_ONSHORE]),
    }


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="minutes") if t else None


def probe_generation(client: EntsoeClient, now: dt.datetime) -> dict[str, Any]:
    """Consulta ligera (últimas 36 h, B18 y B19) y devuelve la observación a guardar."""
    ds = ENTSOE_DATASETS["generation"]
    start = (now - LAG_WINDOW).replace(minute=0, second=0, microsecond=0)
    end = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)
    frames: list[bytes] = []
    for psr in ds.variants:
        try:
            frames.append(client.fetch({**ds.params, "psrType": psr}, start, end))
        except NoDataError:
            continue
    obs = {"observed_at": now.isoformat(timespec="seconds"), "last_step": None}
    if not frames:
        return obs | {"last_b18": None, "last_b19": None}
    return obs | last_published_step(frames)


def append_observation(path: Path, obs: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obs, ensure_ascii=False) + "\n")


def read_observations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # línea truncada por un corte: se ignora
    return out


def observations_from_bronze(bronze: Path, since: dt.date) -> list[dict[str, Any]]:
    """Observaciones equivalentes a la consulta ligera, reconstruidas del bronce de generación.

    Cada carpeta `fetched_at=` desde `since` da una observación con `observed_at = fetched_at`.
    Solo cuenta si `_run.json` dice que la ventana pedida llegaba hasta el día de la descarga
    (`end >= fecha de fetched_at`): una descarga histórica no dice nada del retraso. Sin
    `_run.json` no se puede comprobar y se descarta.
    """
    lo = dt.datetime.combine(since, dt.time(0), tzinfo=dt.UTC)
    chunks: dict[dt.datetime, list[Chunk]] = {}
    for c in list_chunks(bronze, "generation"):
        if c.fetched_at >= lo and not c.nodata:
            chunks.setdefault(c.fetched_at, []).append(c)
    out: list[dict[str, Any]] = []
    for f in list_fetches(bronze, "generation"):
        group = chunks.get(f.fetched_at)
        if not group or f.end is None or f.end < f.fetched_at.date():
            continue
        try:
            last = last_published_step([read_chunk(c) for c in group])
        except pl.exceptions.PolarsError, ValueError, OSError:
            continue  # descarga ilegible: no aporta observación
        out.append(
            {"observed_at": f.fetched_at.isoformat(timespec="seconds")}
            | last
            | {"source": "bronze"}
        )
    return out


def merge_observations(
    probe: list[dict[str, Any]], bronze: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Une ambas fuentes por `observed_at`; en el mismo minuto gana `probe`."""
    by_minute: dict[dt.datetime, dict[str, Any]] = {}
    for o in bronze:
        t = dt.datetime.fromisoformat(o["observed_at"]).replace(second=0, microsecond=0)
        by_minute[t] = {**o, "source": "bronze"}
    for o in probe:
        t = dt.datetime.fromisoformat(o["observed_at"]).replace(second=0, microsecond=0)
        by_minute[t] = {**o, "source": "probe"}
    return sorted(by_minute.values(), key=lambda o: dt.datetime.fromisoformat(o["observed_at"]))


NEAR_CLOSURE = dt.timedelta(hours=2)  # ventana previa al cierre que importa para la persistencia


def lag_minutes(obs: dict[str, Any]) -> float | None:
    if not obs.get("last_step"):
        return None
    seen = dt.datetime.fromisoformat(obs["observed_at"])
    return round((seen - dt.datetime.fromisoformat(obs["last_step"])).total_seconds() / 60, 1)


def _lag_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "median_min": statistics.median(values), "max_min": max(values)}


def lag_report(obs: list[dict[str, Any]], since: dt.date, now: dt.datetime) -> dict[str, Any]:
    """Retraso por observación (resumen) y, por cierre, el último paso visto antes del cierre."""
    lo = dt.datetime.combine(since, dt.time(0), tzinfo=dt.UTC)
    parsed = sorted(
        ((dt.datetime.fromisoformat(o["observed_at"]), o) for o in obs), key=lambda x: x[0]
    )
    parsed = [(t, o) for t, o in parsed if t >= lo]
    lags = [x for _, o in parsed if (x := lag_minutes(o)) is not None]
    days: list[dict[str, Any]] = []
    d = since
    while d <= now.date():
        close = closure_utc(d)
        before = [(t, o) for t, o in parsed if t <= close]
        row: dict[str, Any] = {"closure_day": d.isoformat(), "closure_utc": _iso(close)}
        if not before or (close - before[-1][0]).total_seconds() / 60 > LAG_STALE_MIN:
            if close <= now and parsed:
                days.append(row | {"status": "no_observation"})
            d += dt.timedelta(days=1)
            continue
        t, o = before[-1]
        if not o.get("last_step"):
            days.append(row | {"status": "no_data", "observed_at": t.isoformat(timespec="seconds")})
        else:
            step = dt.datetime.fromisoformat(o["last_step"])
            days.append(
                row
                | {
                    "status": "measured",
                    "observed_at": t.isoformat(timespec="seconds"),
                    "last_step": o["last_step"],
                    "lag_at_closure_min": round((close - step).total_seconds() / 60, 1),
                }
            )
        d += dt.timedelta(days=1)
    at_close = [x["lag_at_closure_min"] for x in days if x["status"] == "measured"]
    near = [
        x
        for t, o in parsed
        if closure_utc(t.date()) - NEAR_CLOSURE <= t <= closure_utc(t.date())
        and (x := lag_minutes(o)) is not None
    ]
    n_source: dict[str, int] = {}
    for _, o in parsed:
        src = o.get("source", "probe")
        n_source[src] = n_source.get(src, 0) + 1
    return {
        "warning": LAG_WARNING,
        "n_observations": len(parsed),
        "n_by_source": n_source,
        "per_observation": _lag_stats(lags),
        "near_closure": _lag_stats(near),
        "at_closure": _lag_stats(at_close),
        "days": days,
    }


def _render_lag(lag: dict[str, Any]) -> list[str]:
    po, ac = lag["per_observation"], lag["at_closure"]
    nc = lag.get("near_closure", {"n": 0})
    src = " ".join(f"{k}={v}" for k, v in lag.get("n_by_source", {}).items())
    lines = ["\n[generación real B18+B19] " + f"observaciones={lag['n_observations']} ({src})"]
    if po["n"]:
        lines.append(
            f"  retraso (observado - paso) mediana/peor: {po['median_min']} / {po['max_min']} min"
        )
    if nc["n"]:
        lines.append(
            f"  retraso en las 2 h previas al cierre (n={nc['n']}) mediana/peor: "
            f"{nc['median_min']} / {nc['max_min']} min"
        )
    if ac["n"]:
        lines.append(f"  retraso al cierre mediana/peor: {ac['median_min']} / {ac['max_min']} min")
    for x in lag["days"]:
        extra = (
            f"  último paso={x['last_step']} retraso={x['lag_at_closure_min']} min"
            if x["status"] == "measured"
            else ""
        )
        lines.append(f"  cierre {x['closure_day']}: {x['status']}{extra}")
    return lines


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
    if "generation_lag" in result:
        lines += _render_lag(result["generation_lag"])
    return "\n".join(lines)
