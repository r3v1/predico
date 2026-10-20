# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false
"""F1.6: validación con Pandera de bronce y plata de la ingesta IFS ENS (solo lectura).

- Plata: un esquema Pandera (backend Polars) por pasada: tipos, n_puntos x 13 pasos filas
  (n_puntos sale del fichero de centroides), clave única, rangos físicos, coherencia de
  estadísticos (p10 ≤ p50 ≤ p90, std ≥ 0) y NaN prohibidos salvo la racha (`wind_gust_10m`)
  anterior al 2024-11-13.
- Bronce: por pasada se resume cada (variable, paso) en una tabla (fracción de NaN, mínimo,
  máximo, miembros sin datos, forma) y esa tabla se valida con Pandera. Así se revisa el último
  paso sin depender de `ingested_forecast_length`, que es NaT, y nunca se carga todo el bronce.
- Huecos: cada día del rango se clasifica y los no cubiertos llevan su causa; se compara la
  cobertura con el umbral de F1 (98 %).

Nunca escribe: el Icechunk se abre con una sesión de solo lectura (la ingesta puede estar
corriendo) y la plata solo se lee.
"""

import argparse
import datetime as dt
import json
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import icechunk
import numpy as np
import numpy.typing as npt
import pandera.polars as pa
import polars as pl
import zarr
from pandera.errors import SchemaErrors

from predico50hz.ingest.dynamical.bronze import BRANCH, init_pos
from predico50hz.ingest.dynamical.config import (
    DEFAULT_BRONZE,
    DEFAULT_CENTROIDS,
    DEFAULT_SILVER,
    DOMAIN,
    LEAD_HOURS,
    N_MEMBERS,
    PINNED_VERSION,
    SPEEDS,
    VARIABLES,
)
from predico50hz.ingest.dynamical.silver import STATS, silver_path
from predico50hz.ingest.ifs.extract import load_points

DEFAULT_OUT_DIR = "data/validation"
MAX_LISTED_GAPS = 15
MIN_COVERAGE = 0.98  # F1: ≥ 98 % de días con pasada 00Z completa
GUST = "wind_gust_10m"
GUST_START = dt.date(2024, 11, 13)  # primera pasada con racha (docs/stage0-dynamical.md)
N_LEADS = len(LEAD_HOURS)
GRID_SHAPE = (27, 31)  # latitud x longitud del dominio recortado

# Rangos físicos de cada variable: observado en stage0 más un margen holgado. Un valor fuera
# solo puede venir de un error de lectura, de unidades o de relleno, no de la meteorología.
RANGES: dict[str, tuple[float, float]] = {
    "wind_u_100m": (-60.0, 60.0),  # observado -22,5..30,0
    "wind_v_100m": (-60.0, 60.0),  # -29,8..30,5
    "wind_u_10m": (-50.0, 50.0),  # -20,5..26,3
    "wind_v_10m": (-50.0, 50.0),  # -17,3..26,8
    "wind_gust_10m": (0.0, 80.0),  # 0,11..41,5
    "temperature_2m": (-45.0, 50.0),  # °C; -16,3..32,3
    "pressure_surface": (85_000.0, 106_000.0),  # Pa; 91 104..103 936
    "temperature_925hpa": (-45.0, 40.0),  # -12,4..25,1
    "temperature_850hpa": (-50.0, 35.0),  # -13,6..19,0
    "pressure_reduced_to_mean_sea_level": (93_000.0, 107_000.0),  # 96 512..104 000
    "wind_speed_100m": (0.0, 70.0),
    "wind_speed_10m": (0.0, 60.0),
}
# Cota superior de la desviación típica del ensemble (muy por encima de la dispersión real).
STD_MAX: dict[str, float] = {
    **{v: 20.0 for v in RANGES if v.startswith("wind_")},
    "temperature_2m": 15.0,
    "temperature_925hpa": 15.0,
    "temperature_850hpa": 15.0,
    "pressure_surface": 3_000.0,
    "pressure_reduced_to_mean_sea_level": 3_000.0,
}
SILVER_VARIABLES: tuple[str, ...] = (*VARIABLES, *SPEEDS)
# Orden de las causas de hueco en el informe (la primera es la principal).
CAUSES = ("no_ingerida", "plata_ausente", "nan", "fuera_de_rango", "falla_esquema")
_NAN_CHECKS = frozenset({"finite", "not_nullable", "dead_members"})


@dataclass(frozen=True)
class Issue:
    layer: str  # "bronce" | "plata"
    cause: str  # una de CAUSES
    check: str
    column: str | None = None
    n: int = 1


def gust_allowed_nan(variable: str, init: dt.date) -> bool:
    """La racha es NaN en toda la pasada antes del 2024-11-13; el resto nunca."""
    return variable == GUST and init < GUST_START


def _cause(check: str) -> str:
    if check in _NAN_CHECKS:
        return "nan"
    if check in {"range", "std_range"}:
        return "fuera_de_rango"
    return "falla_esquema"


def _issues(layer: str, e: SchemaErrors) -> list[Issue]:
    """Agrupa los casos fallidos de Pandera por (check, columna), con su recuento."""
    fc = e.failure_cases
    if not isinstance(fc, pl.DataFrame) or fc.is_empty():
        return [Issue(layer, "falla_esquema", "desconocido")]
    out: list[Issue] = []
    grouped = fc.group_by(["check", "column"], maintain_order=True).len()
    for check, column, n in grouped.iter_rows():
        name = str(check).split(":")[0].strip("<>").removeprefix("Check ").strip()
        name = "dtype" if name.startswith("dtype") else name
        out.append(Issue(layer, _cause(name), name, column, int(n)))
    return out


# --------------------------------------------------------------------------------------
# Plata
# --------------------------------------------------------------------------------------
def _chk(name: str, fn: Any) -> pa.Check:
    return pa.Check(fn, name=name, error=name)


def _finite(d: Any) -> Any:
    return d.lazyframe.select(pl.col(d.key).is_finite())


def _in_range(lo: float, hi: float) -> Any:
    # Los no finitos los reporta "finite"; aquí se ignoran para no contarlos dos veces.
    return lambda d: d.lazyframe.select(
        pl.col(d.key).is_between(lo, hi) | ~pl.col(d.key).is_finite()
    )


def _order(name: str) -> Any:
    p10, p50, p90 = (pl.col(f"{name}_{s}") for s in ("p10", "p50", "p90"))
    return lambda d: d.lazyframe.select(
        ((p10 <= p50) & (p50 <= p90)) | p10.is_nan() | p50.is_nan() | p90.is_nan()
    )


def silver_schema(init: dt.date, n_points: int) -> pa.DataFrameSchema:
    """Esquema de una pasada de plata; depende de `init` (racha y fecha) y de `n_points`."""
    n_rows = n_points * N_LEADS
    ts = pl.Datetime("us")
    cols: dict[str, pa.Column] = {
        "init_time": pa.Column(ts),
        "lead_hours": pa.Column(
            pl.Int32,
            _chk(
                "lead_grid",
                lambda d: d.lazyframe.select(pl.col("lead_hours").is_in(list(LEAD_HOURS))),
            ),
        ),
        "valid_time": pa.Column(ts),
        "point_id": pa.Column(pl.String),
        "lat": pa.Column(pl.Float64, _chk("finite", _finite)),
        "lon": pa.Column(pl.Float64, _chk("finite", _finite)),
    }
    for name in SILVER_VARIABLES:
        lo, hi = RANGES[name]
        nan_ok = gust_allowed_nan(name, init)
        for stat in STATS:
            checks: list[pa.Check] = []
            if stat == "std":
                checks.append(_chk("std_range", _in_range(0.0, STD_MAX[name])))
            else:
                checks.append(_chk("range", _in_range(lo, hi)))
            if not nan_ok:
                checks.append(_chk("finite", _finite))
            cols[f"{name}_{stat}"] = pa.Column(pl.Float32, checks, nullable=nan_ok)

    expected_init = dt.datetime.combine(init, dt.time())
    frame_checks = [
        _chk("n_rows", lambda d: d.lazyframe.select(pl.len() == n_rows)),
        _chk(
            "unique_key",
            lambda d: d.lazyframe.select(pl.struct("point_id", "lead_hours").n_unique() == n_rows),
        ),
        _chk(
            "n_points",
            lambda d: d.lazyframe.select(
                (pl.struct("point_id", "lat", "lon").n_unique() == n_points).alias("coords"),
                (pl.col("point_id").n_unique() == n_points).alias("ids"),
            ).select(pl.all_horizontal(pl.all())),
        ),
        _chk(
            "init_time",
            lambda d: d.lazyframe.select(pl.col("init_time") == expected_init),
        ),
        _chk(
            "valid_time",
            lambda d: d.lazyframe.select(
                pl.col("valid_time")
                == pl.col("init_time") + pl.duration(hours=pl.col("lead_hours"))
            ),
        ),
        *(_chk("stat_order", _order(n)) for n in SILVER_VARIABLES),
    ]
    return pa.DataFrameSchema(cols, checks=frame_checks, strict=True)


def validate_silver(df: pl.DataFrame, init: dt.date, n_points: int) -> list[Issue]:
    try:
        silver_schema(init, n_points).validate(df, lazy=True)
    except SchemaErrors as e:
        return _issues("plata", e)
    except pl.exceptions.PolarsError as e:
        return [Issue("plata", "falla_esquema", "polars", str(e)[:120])]
    return []


# --------------------------------------------------------------------------------------
# Bronce
# --------------------------------------------------------------------------------------
def bronze_pass_stats(
    arrays: Mapping[str, npt.NDArray[np.floating]], init: dt.date
) -> pl.DataFrame:
    """Resumen por (variable, paso) de una pasada; `arrays[v]` es (lead, member, lat, lon)."""
    rows: list[dict[str, Any]] = []
    for v, a in arrays.items():
        finite = np.isfinite(a)
        n_lead, n_mem, n_lat, n_lon = a.shape
        nan_frac = 1.0 - finite.mean(axis=(1, 2, 3))
        vmin = np.where(finite, a, np.inf).min(axis=(1, 2, 3))
        vmax = np.where(finite, a, -np.inf).max(axis=(1, 2, 3))
        dead = (finite.sum(axis=(2, 3)) == 0).sum(axis=1)  # miembros sin ningún dato válido
        lo, hi = RANGES.get(v, (-np.inf, np.inf))
        for k in range(n_lead):
            has = bool(np.isfinite(vmin[k]))
            rows.append(
                {
                    "variable": v,
                    "lead_hours": LEAD_HOURS[k] if k < N_LEADS else -1,
                    "nan_frac": float(nan_frac[k]),
                    "vmin": float(vmin[k]) if has else None,
                    "vmax": float(vmax[k]) if has else None,
                    "dead_members": int(dead[k]),
                    "n_members": n_mem,
                    "n_lat": n_lat,
                    "n_lon": n_lon,
                    "lo": float(lo),
                    "hi": float(hi),
                    "nan_ok": gust_allowed_nan(v, init),
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "variable": pl.String,
            "lead_hours": pl.Int64,
            "nan_frac": pl.Float64,
            "vmin": pl.Float64,
            "vmax": pl.Float64,
            "dead_members": pl.Int64,
            "n_members": pl.Int64,
            "n_lat": pl.Int64,
            "n_lon": pl.Int64,
            "lo": pl.Float64,
            "hi": pl.Float64,
            "nan_ok": pl.Boolean,
        },
    )


def _bronze_schema(variables: tuple[str, ...]) -> pa.DataFrameSchema:
    def nan_ok(d: Any) -> Any:
        c = d.lazyframe
        return c.select(
            (pl.col("nan_frac") == 0.0) | (pl.col("nan_ok") & (pl.col("nan_frac") == 1.0))
        )

    def dead(d: Any) -> Any:
        return d.lazyframe.select((pl.col("dead_members") == 0) | pl.col("nan_ok"))

    def rng(d: Any) -> Any:
        return d.lazyframe.select(
            pl.col("vmin").is_null()
            | ((pl.col("vmin") >= pl.col("lo")) & (pl.col("vmax") <= pl.col("hi")))
        )

    n_expected = len(variables) * N_LEADS
    return pa.DataFrameSchema(
        {
            "variable": pa.Column(pl.String, pa.Check.isin(list(variables))),
            "lead_hours": pa.Column(pl.Int64, pa.Check.isin(list(LEAD_HOURS))),
            "nan_frac": pa.Column(pl.Float64),
            "vmin": pa.Column(pl.Float64, nullable=True),
            "vmax": pa.Column(pl.Float64, nullable=True),
            "dead_members": pa.Column(pl.Int64),
            "n_members": pa.Column(
                pl.Int64,
                _chk("members", lambda d: d.lazyframe.select(pl.col("n_members") == N_MEMBERS)),
            ),
            "n_lat": pa.Column(
                pl.Int64,
                _chk("grid", lambda d: d.lazyframe.select(pl.col("n_lat") == GRID_SHAPE[0])),
            ),
            "n_lon": pa.Column(
                pl.Int64,
                _chk("grid", lambda d: d.lazyframe.select(pl.col("n_lon") == GRID_SHAPE[1])),
            ),
            "lo": pa.Column(pl.Float64),
            "hi": pa.Column(pl.Float64),
            "nan_ok": pa.Column(pl.Boolean),
        },
        checks=[
            _chk("n_rows", lambda d: d.lazyframe.select(pl.len() == n_expected)),
            _chk(
                "unique_key",
                lambda d: d.lazyframe.select(
                    pl.struct("variable", "lead_hours").n_unique() == n_expected
                ),
            ),
            _chk("finite", nan_ok),
            _chk("dead_members", dead),
            _chk("range", rng),
        ],
        strict=True,
    )


def validate_bronze_stats(
    stats: pl.DataFrame, variables: tuple[str, ...] = VARIABLES
) -> list[Issue]:
    try:
        _bronze_schema(variables).validate(stats, lazy=True)
    except SchemaErrors as e:
        return _enrich_bronze(_issues("bronce", e), stats)
    return []


def _enrich_bronze(issues: list[Issue], stats: pl.DataFrame) -> list[Issue]:
    """Pone en `column` las variables afectadas (el esquema comprueba por filas, no por columna)."""
    bad: dict[str, list[str]] = {
        "finite": stats.filter(
            ~((pl.col("nan_frac") == 0.0) | (pl.col("nan_ok") & (pl.col("nan_frac") == 1.0)))
        )["variable"]
        .unique()
        .sort()
        .to_list(),
        "dead_members": stats.filter((pl.col("dead_members") > 0) & ~pl.col("nan_ok"))["variable"]
        .unique()
        .sort()
        .to_list(),
        "range": stats.filter(
            pl.col("vmin").is_not_null()
            & ((pl.col("vmin") < pl.col("lo")) | (pl.col("vmax") > pl.col("hi")))
        )["variable"]
        .unique()
        .sort()
        .to_list(),
    }
    out: list[Issue] = []
    for i in issues:
        names = bad.get(i.check)
        out.append(Issue(i.layer, i.cause, i.check, ",".join(names) if names else i.column, i.n))
    return out


# --------------------------------------------------------------------------------------
# Lectura de solo lectura y orquestación
# --------------------------------------------------------------------------------------
def open_bronze_readonly(path: str | Path) -> tuple[zarr.Group, str]:
    """Abre el bronce SIN crearlo ni escribir: sesión de solo lectura sobre un snapshot fijo."""
    repo = icechunk.Repository.open(icechunk.local_filesystem_storage(str(path)))
    session = repo.readonly_session(branch=BRANCH)
    return zarr.open_group(session.store, mode="r"), session.snapshot_id


def check_bronze_structure(root: zarr.Group) -> list[str]:
    """Comprobaciones globales de forma y ejes del bronce; devuelve los problemas en texto."""
    problems: list[str] = []
    if root.attrs.get("source_dataset_version") != PINNED_VERSION:
        problems.append(f"source_dataset_version {root.attrs.get('source_dataset_version')!r}")
    lat, lon = np.asarray(root["latitude"][:]), np.asarray(root["longitude"][:])
    if (len(lat), len(lon)) != GRID_SHAPE:
        problems.append(f"malla {len(lat)}x{len(lon)} != {GRID_SHAPE}")
    if not np.all(np.diff(lat) > 0):
        problems.append("latitud no ascendente")
    tol = 0.01
    if abs(lat[0] - DOMAIN.lat_min) > 0.25 + tol or abs(lat[-1] - DOMAIN.lat_max) > 0.25 + tol:
        problems.append(f"latitud {lat[0]}..{lat[-1]} fuera del dominio configurado")
    if abs(lon[0] - DOMAIN.lon_min) > 0.25 + tol or abs(lon[-1] - DOMAIN.lon_max) > 0.25 + tol:
        problems.append(f"longitud {lon[0]}..{lon[-1]} fuera del dominio configurado")
    if list(np.asarray(root["lead_time"][:])) != list(LEAD_HOURS):
        problems.append("lead_time distinto de +18..+54 h cada 3 h")
    if list(np.asarray(root["ensemble_member"][:])) != list(range(N_MEMBERS)):
        problems.append(f"ensemble_member distinto de 0..{N_MEMBERS - 1}")
    for v in VARIABLES:
        if v not in root:
            problems.append(f"falta la variable {v}")
        elif tuple(root[v].shape[1:]) != (N_LEADS, N_MEMBERS, *GRID_SHAPE):
            problems.append(f"{v}: forma {root[v].shape}")
    return problems


@dataclass
class DayResult:
    init_date: str
    bronze_ingested: bool = False
    silver_present: bool = False
    issues: list[Issue] = field(default_factory=list)
    bronze_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    bronze_nan: dict[str, float] = field(default_factory=dict)
    silver_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def covered(self) -> bool:
        return self.bronze_ingested and self.silver_present and not self.issues

    @property
    def causes(self) -> list[str]:
        found = {i.cause for i in self.issues}
        return [c for c in CAUSES if c in found]


def _silver_ranges(df: pl.DataFrame) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for name in SILVER_VARIABLES:
        cols = [f"{name}_{s}" for s in STATS if s != "std"]
        if not all(c in df.columns for c in cols):
            continue
        sub = df.select(cols).to_numpy()
        if np.isfinite(sub).any():
            out[name] = (float(np.nanmin(sub)), float(np.nanmax(sub)))
    return out


def validate_day(  # noqa: PLR0913, PLR0917
    root: zarr.Group,
    ingested: bool,
    silver_root: Path,
    day: dt.date,
    n_points: int,
    provenance: frozenset[str] = frozenset(),
) -> DayResult:
    res = DayResult(day.isoformat(), bronze_ingested=ingested)
    if ingested:
        i = init_pos(day)
        arrays = {v: np.asarray(root[v][i]) for v in VARIABLES}
        stats = bronze_pass_stats(arrays, day)
        res.issues += validate_bronze_stats(stats)
        agg = stats.group_by("variable").agg(
            pl.col("vmin").min(), pl.col("vmax").max(), pl.col("nan_frac").max()
        )
        for v, lo, hi, nf in agg.iter_rows():
            res.bronze_nan[v] = float(nf)
            if lo is not None:
                res.bronze_ranges[v] = (float(lo), float(hi))
        if day.isoformat() not in provenance:
            res.issues.append(Issue("bronce", "falla_esquema", "provenance"))
    else:
        res.issues.append(Issue("bronce", "no_ingerida", "ingested"))

    path = silver_path(silver_root, day.isoformat())
    if not path.exists():
        res.issues.append(Issue("plata", "plata_ausente", "part-0.parquet"))
        return res
    res.silver_present = True
    try:
        df = pl.read_parquet(path)
    except (pl.exceptions.PolarsError, OSError) as e:
        res.issues.append(Issue("plata", "falla_esquema", "ilegible", str(e)[:120]))
        return res
    res.issues += validate_silver(df, day, n_points)
    res.silver_ranges = _silver_ranges(df)
    return res


def _merge_ranges(results: list[DayResult], attr: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in results:
        for v, (lo, hi) in getattr(r, attr).items():
            cur = out.setdefault(v, {"min": lo, "max": hi})
            cur["min"], cur["max"] = min(cur["min"], lo), max(cur["max"], hi)
    return out


def build_report(
    results: list[DayResult],
    start: dt.date,
    end: dt.date,
    meta: dict[str, Any],
    min_coverage: float = MIN_COVERAGE,
) -> dict[str, Any]:
    n = len(results)
    covered = sum(r.covered for r in results)
    gaps = [r for r in results if not r.covered]
    by_cause: dict[str, int] = {}
    for r in gaps:
        for c in r.causes[:1]:  # solo la causa principal, para que la suma cuadre
            by_cause[c] = by_cause.get(c, 0) + 1
    coverage = covered / n if n else 0.0
    # Variables con NaN en bronce, tras descontar la racha anterior al 2024-11-13 (esperada).
    nan_max: dict[str, float] = {}
    for r in results:
        for v, f in r.bronze_nan.items():
            if gust_allowed_nan(v, dt.date.fromisoformat(r.init_date)):
                continue
            nan_max[v] = max(nan_max.get(v, 0.0), f)
    return {
        **meta,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n_days": n,
        "n_covered": covered,
        "coverage": coverage,
        "min_coverage": min_coverage,
        "meets_threshold": coverage >= min_coverage,
        "bronze_ingested_days": sum(r.bronze_ingested for r in results),
        "silver_present_days": sum(r.silver_present for r in results),
        "gaps_by_main_cause": by_cause,
        "gaps": [
            {
                "init_date": r.init_date,
                "causes": r.causes,
                "issues": [asdict(i) for i in r.issues],
            }
            for r in gaps
        ],
        "bronze_nan_frac_max_excl_pre_gust": nan_max,
        "bronze_observed_ranges": _merge_ranges(results, "bronze_ranges"),
        "silver_observed_ranges": _merge_ranges(results, "silver_ranges"),
        "limits": {"ranges": RANGES, "std_max": STD_MAX},
    }


def run(  # noqa: PLR0913, PLR0917
    start: dt.date,
    end: dt.date,
    bronze: str | Path = DEFAULT_BRONZE,
    silver: str | Path = DEFAULT_SILVER,
    workers: int = 4,
    min_coverage: float = MIN_COVERAGE,
    centroids: str | Path = DEFAULT_CENTROIDS,
) -> dict[str, Any]:
    if end < start:
        raise ValueError("--end anterior a --start")
    _ = (init_pos(start), init_pos(end))  # falla pronto si el rango sale del eje del bronce
    n_points = len(load_points(centroids))
    root, snapshot = open_bronze_readonly(bronze)
    structure = check_bronze_structure(root)
    flags = np.asarray(root["ingested"][:])
    provenance = frozenset(root.attrs.get("provenance", {}))  # pyright: ignore[reportArgumentType]
    days = [start + dt.timedelta(days=k) for k in range((end - start).days + 1)]

    def one(day: dt.date) -> DayResult:
        return validate_day(
            root, bool(flags[init_pos(day)]), Path(silver), day, n_points, provenance
        )

    results: list[DayResult] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, r in enumerate(ex.map(one, days), 1):
            results.append(r)
            if k % 25 == 0 or k == len(days):
                print(f"  {k}/{len(days)} pasadas revisadas", file=sys.stderr, flush=True)
    meta = {
        "bronze": str(bronze),
        "bronze_snapshot_id": snapshot,
        "silver": str(silver),
        "centroids": str(centroids),
        "n_points": n_points,
        "bronze_structure_problems": structure,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    return build_report(results, start, end, meta, min_coverage)


def summarize(rep: dict[str, Any]) -> str:
    lines = [
        f"Validación IFS ENS {rep['start']}..{rep['end']} "
        f"(snapshot bronce {rep['bronze_snapshot_id']})",
        f"  días: {rep['n_days']}  cubiertos (bronce+plata válidos): {rep['n_covered']}"
        f"  cobertura: {rep['coverage']:.1%}  umbral {rep['min_coverage']:.0%}: "
        f"{'CUMPLE' if rep['meets_threshold'] else 'NO CUMPLE'}",
        f"  bronce ingerido: {rep['bronze_ingested_days']}  plata presente: "
        f"{rep['silver_present_days']}",
        f"  huecos por causa principal: {rep['gaps_by_main_cause'] or 'ninguno'}",
    ]
    if rep["bronze_structure_problems"]:
        lines.append(f"  ESTRUCTURA BRONCE: {rep['bronze_structure_problems']}")
    for g in rep["gaps"][:MAX_LISTED_GAPS]:
        lines.append(f"    {g['init_date']}: {','.join(g['causes'])}")
    if len(rep["gaps"]) > MAX_LISTED_GAPS:
        lines.append(f"    ... y {len(rep['gaps']) - MAX_LISTED_GAPS} más (ver JSON)")
    return "\n".join(lines)


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_date, required=True)
    ap.add_argument("--end", type=_date, required=True)
    ap.add_argument("--bronze", default=DEFAULT_BRONZE)
    ap.add_argument("--silver", default=DEFAULT_SILVER)
    ap.add_argument("--centroids", default=DEFAULT_CENTROIDS)
    ap.add_argument("--workers", type=int, default=4, help="pasadas validadas a la vez")
    ap.add_argument("--min-coverage", type=float, default=MIN_COVERAGE)
    ap.add_argument("--out", help="informe JSON (por defecto data/validation/ifs_ens_<rango>.json)")
    args = ap.parse_args(argv)
    rep = run(
        args.start,
        args.end,
        args.bronze,
        args.silver,
        args.workers,
        args.min_coverage,
        args.centroids,
    )
    out = Path(args.out or f"{DEFAULT_OUT_DIR}/ifs_ens_{args.start}_{args.end}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    print(summarize(rep))
    print(f"  informe: {out}")
    return 0 if rep["meets_threshold"] and not rep["bronze_structure_problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
