# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false
"""Esquemas Pandera de la plata de MaStR (unidades y capacidad) con listado de incidencias.

Los esquemas fallan en lo que haría inservible el dato (coordenadas fuera del dominio, potencias
no positivas o ≥ 20 MW por unidad, fechas incoherentes, claves duplicadas, capacidad negativa).
"""

import datetime as dt
from typing import Any

import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from predico50hz.ingest.mastr.config import LAT_RANGE, LON_RANGE

MAX_UNIT_MW = 20.0  # la mayor turbina eólica comercial ronda 15-26 MW; 15 MW el máximo observado
MIN_START = dt.date(
    1940, 1, 1
)  # caza fechas absurdas (1900, 0001); hay una micro-unidad de 1 kW de 1949
STATUSES = ["In Betrieb", "Vorübergehend stillgelegt", "Endgültig stillgelegt"]
COORD_SOURCES = ["unit", "unit_swapped", "plz", "gemeinde", "landkreis", "bundesland"]


def _chk(name: str, fn: Any) -> pa.Check:
    return pa.Check(fn, name=name, error=name)


def units_schema(export_date: dt.date) -> pa.DataFrameSchema:
    """Unidades de la zona de 50Hertz (`in_zone`): lo que alimenta clústeres y capacidad."""
    return pa.DataFrameSchema(
        {
            "unit_id": pa.Column(pl.String),
            "kind": pa.Column(pl.String, pa.Check.isin(["land", "sea"])),
            "status": pa.Column(pl.String, pa.Check.isin(STATUSES)),
            "lat": pa.Column(pl.Float64, pa.Check.between(*LAT_RANGE), nullable=True),
            "lon": pa.Column(pl.Float64, pa.Check.between(*LON_RANGE), nullable=True),
            "coord_source": pa.Column(pl.String, pa.Check.isin(COORD_SOURCES), nullable=True),
            "net_mw": pa.Column(
                pl.Float64, pa.Check.between(0.0, MAX_UNIT_MW, include_min=False, include_max=False)
            ),
            "gross_mw": pa.Column(
                pl.Float64, pa.Check.between(0.0, MAX_UNIT_MW, include_min=False, include_max=False)
            ),
            "start_date": pa.Column(pl.Date, pa.Check.between(MIN_START, export_date)),
        },
        checks=[
            _chk("unique_unit_id", lambda d: d.lazyframe.select(pl.col("unit_id").is_unique())),
            _chk(
                "end_not_before_start",
                lambda d: d.lazyframe.select(
                    (
                        pl.col("end_date").is_null() | (pl.col("end_date") >= pl.col("start_date"))
                    ).all()
                ),
            ),
            _chk(
                "decommissioned_has_end_date",
                lambda d: d.lazyframe.select(
                    (
                        (pl.col("status") != "Endgültig stillgelegt")
                        | pl.col("end_date").is_not_null()
                    ).all()
                ),
            ),
            _chk(
                "coords_iff_source",
                lambda d: d.lazyframe.select(
                    (pl.col("lat").is_null() == pl.col("coord_source").is_null()).all()
                ),
            ),
        ],
        strict=False,
    )


def capacity_schema(group: str) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "date": pa.Column(pl.Date),
            group: pa.Column(pl.String),
            "capacity_mw": pa.Column(pl.Float64, pa.Check.between(0.0, 40_000.0)),
            "n_units": pa.Column(pl.Int64, pa.Check.ge(0)),
        },
        checks=[
            _chk(
                "unique_key",
                lambda d: d.lazyframe.select(pl.struct("date", group).n_unique() == pl.len()),
            ),
            _chk(
                "daily_grid",
                lambda d: d.lazyframe.select(
                    pl.col("date")
                    .unique()
                    .sort()
                    .diff()
                    .drop_nulls()
                    .eq(pl.lit(dt.timedelta(1)))
                    .all()
                ),
            ),
        ],
        strict=True,
    )


def schema_issues(schema: pa.DataFrameSchema, df: pl.DataFrame) -> list[dict[str, Any]]:
    try:
        schema.validate(df, lazy=True)
    except SchemaErrors as e:
        fc = e.failure_cases
        if isinstance(fc, pl.DataFrame) and not fc.is_empty():
            g = fc.group_by(["check", "column"], maintain_order=True).len()
            return [{"check": str(c), "column": col, "n": int(n)} for c, col, n in g.iter_rows()]
        return [{"check": "desconocido", "column": None, "n": 1}]
    except pl.exceptions.PolarsError as e:
        return [{"check": "polars", "column": None, "n": 1, "detail": str(e)[:200]}]
    return []
