# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# ruff: noqa: E501, PLR2004
"""Calidad de los datos: cobertura diaria por fuente, controles superados y huecos con su causa."""

import datetime as dt
from typing import Any

import polars as pl

from predico50hz.report.paths import Paths
from predico50hz.report.util import has_delta, read_json

CALENDAR_START = dt.date(2015, 1, 1)
NWP_START = dt.date(2024, 4, 1)
# Códigos del calendario: 0 sin datos esperados, 1 completo, 2 parcial o rellenado, 3 hueco, 4 pendiente
NONE, OK, PARTIAL, GAP, PENDING = range(5)
GAP_CAUSES = {
    "no_ingerida": "La previsión de ese día no se había descargado",
    "plata_ausente": "La previsión se descargó pero no se pudo resumir",
    "nan": "Faltaban valores en la previsión",
    "fuera_de_rango": "Había valores fuera de lo físicamente posible",
    "falla_esquema": "El formato de los datos no era el esperado",
}


def _days(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def _encode(days: list[dt.date], codes: dict[dt.date, int], default: int = NONE) -> str:
    return "".join(str(codes.get(d, default)) for d in days)


def _count_by_day(df: pl.DataFrame, col: str = "ts_utc") -> dict[dt.date, int]:
    g = df.with_columns(pl.col(col).dt.date().alias("_d")).group_by("_d").len()
    return dict(g.iter_rows())


def latest_ifs_report(paths: Paths) -> dict[str, Any] | None:
    reports = [r for p in sorted(paths.validation.glob("ifs_ens_*.json")) if (r := read_json(p))]
    return (
        max(reports, key=lambda r: (r.get("n_days", 0), r.get("generated_at", "")))
        if reports
        else None
    )


def _nwp_row(paths: Paths, days: list[dt.date], report: dict[str, Any] | None) -> dict[str, Any]:
    root = paths.silver / "ifs_ens_clusters"
    present = (
        {dt.date.fromisoformat(p.name.split("=", 1)[1]) for p in root.glob("init_date=*")}
        if root.is_dir()
        else set()
    )
    codes: dict[dt.date, int] = {}
    if present:
        first, last = min(present), max(present)
        gaps = {dt.date.fromisoformat(g["init_date"]) for g in (report or {}).get("gaps", [])}
        for d in days:
            if d < NWP_START or d > last:
                continue
            if d < first:
                codes[d] = PENDING
            elif d in gaps or d not in present:
                codes[d] = GAP
            else:
                codes[d] = OK
    return {
        "key": "nwp",
        "label": "Previsión meteorológica",
        "codes": _encode(days, codes),
        "n_ok": len(present),
    }


def _generation_row(paths: Paths, days: list[dt.date], today: dt.date) -> dict[str, Any]:
    p = paths.silver / "entsoe" / "generation"
    codes: dict[dt.date, int] = {}
    if has_delta(p):
        df = pl.read_delta(str(p)).with_columns(pl.col("ts_utc").dt.date().alias("_d"))
        agg = df.group_by("_d").agg(
            pl.len().alias("n"),
            (pl.col("is_gap_filled").fill_null(False)).sum().alias("filled"),
        )
        for d, n, filled in agg.iter_rows():
            if d >= today:
                continue
            codes[d] = GAP if n == 0 else PARTIAL if (n < 96 or filled) else OK
    return {
        "key": "generation",
        "label": "Producción real (ENTSO-E)",
        "codes": _encode(days, codes),
    }


def _prices_row(paths: Paths, days: list[dt.date], today: dt.date) -> dict[str, Any]:
    p = paths.silver / "entsoe" / "prices"
    codes: dict[dt.date, int] = {}
    if has_delta(p):
        df = pl.read_delta(str(p)).with_columns(pl.col("ts_utc").dt.date().alias("_d"))
        agg = df.group_by("_d").agg(
            pl.len().alias("n"), pl.col("resolution_min").min().alias("res")
        )
        for d, n, res in agg.iter_rows():
            if d >= today:
                continue
            codes[d] = OK if n >= 1440 // max(res, 1) else PARTIAL
        if codes:
            first = min(codes)
            for d in days:
                if first < d < today and d not in codes:
                    codes[d] = GAP
    return {
        "key": "prices",
        "label": "Precio de la electricidad (ENTSO-E)",
        "codes": _encode(days, codes),
    }


def _forecast_row(paths: Paths, days: list[dt.date], today: dt.date) -> dict[str, Any]:
    p = paths.silver / "entsoe" / "forecast"
    codes: dict[dt.date, int] = {}
    if has_delta(p):
        df = pl.read_delta(str(p)).select("ts_utc", "psr_type").unique()
        for d, n in _count_by_day(df).items():
            if d >= today:
                continue
            codes[d] = OK if n >= 192 else PARTIAL
    return {
        "key": "forecast",
        "label": "Previsión del operador (ENTSO-E)",
        "codes": _encode(days, codes),
    }


def _mastr_row(paths: Paths, days: list[dt.date]) -> dict[str, Any]:
    p = paths.silver / "mastr" / "capacity_total"
    codes: dict[dt.date, int] = {}
    if has_delta(p):
        for (d,) in pl.read_delta(str(p)).select("date").unique().iter_rows():
            codes[d] = OK
    return {"key": "mastr", "label": "Parques eólicos (MaStR)", "codes": _encode(days, codes)}


def _checks(paths: Paths) -> list[dict[str, Any]]:
    """Controles de las validaciones; cada uno es superado, con aviso o fallido."""
    out: list[dict[str, Any]] = []
    names = {
        "generation": "Producción real",
        "prices": "Precios",
        "forecast": "Previsión del operador",
        "capacity": "Capacidad oficial",
    }
    for key, label in names.items():
        r = read_json(paths.validation / f"entsoe_{key}.json")
        if not r:
            continue
        out.append(
            {"source": label, "check": "Formato y tipos de datos", "ok": bool(r.get("schema_ok"))}
        )
        rev = r.get("revisions") or {}
        out.append(
            {
                "source": label,
                "check": "Sin cambios entre descargas",
                "ok": rev.get("keys_revised", 0) == 0,
            }
        )
        n_gaps = (r.get("gaps") or {}).get("n")
        if n_gaps is None and "series" in r:
            n_gaps = sum(s.get("gap_runs", 0) for s in r["series"].values())
        if n_gaps is not None:
            out.append(
                {
                    "source": label,
                    "check": "Sin huecos en la serie",
                    "ok": n_gaps == 0,
                    "warn": n_gaps > 0,
                }
            )
    m = read_json(paths.validation / "mastr.json")
    if m:
        issues = m.get("schema_issues") or {}
        out.append(
            {
                "source": "Parques eólicos",
                "check": "Formato y tipos de datos",
                "ok": bool(m.get("schema_ok")) and not any(issues.values()),
            }
        )
        out.append(
            {
                "source": "Parques eólicos",
                "check": "Todas las turbinas asignadas a un grupo",
                "ok": not m.get("units_without_cluster_in_zone"),
            }
        )
    i = latest_ifs_report(paths)
    if i:
        out.append(
            {
                "source": "Previsión meteorológica",
                "check": "Estructura del almacén de datos",
                "ok": not i.get("bronze_structure_problems"),
            }
        )
        out.append(
            {
                "source": "Previsión meteorológica",
                "check": "Cobertura mínima del 98 %",
                "ok": bool(i.get("meets_threshold")),
            }
        )
        out.append(
            {
                "source": "Previsión meteorológica",
                "check": "Sin días con huecos",
                "ok": not i.get("gaps"),
            }
        )
        nans = i.get("bronze_nan_frac_max_excl_pre_gust") or {}
        out.append(
            {
                "source": "Previsión meteorológica",
                "check": "Sin valores vacíos en las variables",
                "ok": all(v == 0 for v in nans.values()),
            }
        )
    return out


def _gaps(paths: Paths, nwp_first: dt.date | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = latest_ifs_report(paths)
    for g in (i or {}).get("gaps", []):
        causes = [GAP_CAUSES.get(c, str(c)) for c in g.get("causes", [])]
        out.append(
            {
                "level": "bad",
                "title": f"Previsión meteorológica del {g['init_date']}",
                "text": "; ".join(causes) + ".",
            }
        )
    if nwp_first and nwp_first > NWP_START:
        n = (nwp_first - NWP_START).days
        out.append(
            {
                "level": "warn",
                "title": f"Previsión meteorológica de {NWP_START.isoformat()} a {(nwp_first - dt.timedelta(days=1)).isoformat()}",
                "text": f"Aún sin descargar ({n} días): está prevista como etapa 3 del plan. No es un fallo, es trabajo pendiente.",
            }
        )
    f = read_json(paths.validation / "entsoe_forecast.json")
    for g in ((f or {}).get("gaps") or {}).get("longest", []):
        out.append(
            {
                "level": "warn",
                "title": f"Previsión del operador, tras el {g['after'][:10]}",
                "text": "Falta un tramo de la previsión del operador para los aerogeneradores marinos "
                f"({g['missing_steps']} cuartos de hora, un día completo). Solo afecta a la comparación con el operador.",
            }
        )
    gen = read_json(paths.validation / "entsoe_generation.json")
    off = ((gen or {}).get("series") or {}).get("offshore")
    if off:
        src = off.get("steps_by_source", {})
        total = sum(src.values()) or 1
        a03 = src.get("a03", 0)
        out.append(
            {
                "level": "info",
                "title": "Producción marina: valores repetidos",
                "text": f"El {a03 / total * 100:.1f} % de los cuartos de hora marinos ".replace(
                    ".", ","
                )
                + "no llegan de la fuente porque la API no repite valores iguales; se reconstruyen arrastrando el último, "
                "igual que hace la liga. No son huecos.",
            }
        )
    return out


def aggregate(paths: Paths, today: dt.date) -> dict[str, Any]:
    days = _days(CALENDAR_START, today)
    report = latest_ifs_report(paths)
    nwp = _nwp_row(paths, days, report)
    rows = [
        nwp,
        _generation_row(paths, days, today),
        _prices_row(paths, days, today),
        _forecast_row(paths, days, today),
        _mastr_row(paths, days),
    ]
    root = paths.silver / "ifs_ens_clusters"
    firsts = sorted(root.glob("init_date=*")) if root.is_dir() else []
    nwp_first = dt.date.fromisoformat(firsts[0].name.split("=", 1)[1]) if firsts else None
    checks = _checks(paths)
    return {
        "start": CALENDAR_START.isoformat(),
        "n_days": len(days),
        "rows": rows,
        "checks": checks,
        "checks_ok": sum(1 for c in checks if c["ok"]),
        "checks_total": len(checks),
        "coverage": report.get("coverage") if report else None,
        "coverage_days": [report.get("n_covered"), report.get("n_days")] if report else None,
        "coverage_range": [report.get("start"), report.get("end")] if report else None,
        "gaps": _gaps(paths, nwp_first),
    }
