# pyright: basic
"""Coherencia de la capacidad de MaStR con la de ENTSO-E y con la generación observada.

ENTSO-E (A68/A33, anual) publica un valor por año natural. Empíricamente (ver
`docs/f2.6-mastr.md`) coincide con el estado de MaStR el **1 de enero** de ese año (valor del día
1 de enero, tomado al cierre); aquí se compara con esa y con la del 31 de diciembre.
"""

import datetime as dt
from typing import Any, cast

import polars as pl

PSR = {"sea": "B18", "land": "B19"}
GEN_COL = {"sea": "wind_offshore_mw", "land": "wind_onshore_mw"}
THRESHOLD_PCT = 5.0


def _cap_at(total: pl.DataFrame, kind: str, day: dt.date) -> float | None:
    r = total.filter((pl.col("kind") == kind) & (pl.col("date") == day))["capacity_mw"]
    return float(r[0]) if len(r) else None


def capacity_vs_entsoe(total: pl.DataFrame, entsoe: pl.DataFrame) -> list[dict[str, Any]]:
    """Por año y tipo: capacidad ENTSO-E, MaStR a 1 ene y a 31 dic, y desviación en %."""
    last = cast(dt.date, total["date"].max())
    rows: list[dict[str, Any]] = []
    for r in entsoe.sort("year", "psr_type").iter_rows(named=True):
        kind = "sea" if r["psr_type"] == PSR["sea"] else "land"
        y = int(r["year"])
        jan1 = _cap_at(total, kind, dt.date(y, 1, 1))
        dec31 = _cap_at(total, kind, min(dt.date(y, 12, 31), last))
        e = float(r["installed_mw"])
        rows.append(
            {
                "year": y,
                "kind": kind,
                "entsoe_mw": e,
                "mastr_jan1_mw": jan1,
                "mastr_dec31_mw": dec31,
                "dev_jan1_pct": None if jan1 is None else 100.0 * (jan1 - e) / e,
                "dev_dec31_pct": None if dec31 is None else 100.0 * (dec31 - e) / e,
                "dec31_is_partial": dt.date(y, 12, 31) > last,
            }
        )
    return rows


def convention_mape(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Desviación absoluta media (%) por tipo y convención, sin el año parcial en 31 dic."""
    out: dict[str, dict[str, float]] = {}
    for kind in ("land", "sea"):
        sel = [r for r in rows if r["kind"] == kind and r["dev_jan1_pct"] is not None]
        full = [r for r in sel if not r["dec31_is_partial"]]
        out[kind] = {
            "mape_jan1_pct": sum(abs(r["dev_jan1_pct"]) for r in sel) / max(len(sel), 1),
            "mape_dec31_pct": sum(abs(r["dev_dec31_pct"]) for r in full) / max(len(full), 1),
        }
    return out


def generation_vs_capacity(gen: pl.DataFrame, total: pl.DataFrame) -> list[dict[str, Any]]:
    """Generación máxima por año frente a la capacidad MaStR del día en que ocurre."""
    cap = total.pivot(on="kind", index="date", values="capacity_mw")
    g = gen.with_columns(date=pl.col("ts_utc").dt.date()).join(cap, on="date", how="left")
    rows: list[dict[str, Any]] = []
    for kind, col in GEN_COL.items():
        per = g.drop_nulls([col, kind]).with_columns(
            ratio=pl.col(col) / pl.col(kind), year=pl.col("ts_utc").dt.year()
        )
        agg = per.group_by("year").agg(
            max_gen_mw=pl.col(col).max(),
            max_ratio=pl.col("ratio").max(),
            p999_ratio=pl.col("ratio").quantile(0.999),
            steps_over_capacity=(pl.col("ratio") > 1.0).sum(),
            steps=pl.len(),
        )
        rows += [{"kind": kind, **r} for r in agg.sort("year").iter_rows(named=True)]
    return rows
