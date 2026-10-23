# ruff: noqa: PLR0913, PLR0917, RUF002
"""Plata sintética mínima para las pruebas de la capa de variables (sin depender de `data/`)."""

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml
from pydantic_settings import BaseSettings

from predico50hz import settings as _settings  # noqa: F401 (registra la subclase)
from predico50hz.features.build import Sources
from predico50hz.features.nwp import INSTANT_VARS, LEADS
from predico50hz.orchestration import settings as _ops_settings  # noqa: F401 (registra la subclase)

STATS = ("ctrl", "mean", "std", "p10", "p50", "p90")
POINTS = [("lan_a", "land", 1000.0), ("lan_b", "land", 3000.0), ("sea_a", "sea", 2000.0)]
# Cierres con 96, 92 y 100 pasos, verano e invierno.
DATES = [
    dt.date(2025, 10, 25),
    dt.date(2026, 1, 15),
    dt.date(2026, 3, 28),
    dt.date(2026, 7, 10),
]
CAP_LAST = dt.date(2026, 7, 10)  # último día de MaStR sintético


def _value(var: str, stat: str, p: int, lead: int, day: int, bump: float = 0.0) -> float:
    base = {
        "wind_speed_100m": 8.0,
        "wind_speed_10m": 4.0,
        "wind_u_100m": 5.0,
        "wind_v_100m": -3.0,
        "wind_u_10m": 2.5,
        "wind_v_10m": -1.5,
        "wind_gust_10m": 11.0,
        "temperature_2m": 8.0,
        "temperature_925hpa": 6.0,
        "temperature_850hpa": 3.0,
        "pressure_surface": 100500.0,
        "pressure_reduced_to_mean_sea_level": 101300.0,
    }[var]
    wave = 1.0 + 0.2 * np.sin(lead / 7.0 + p + day) + bump
    off = {"ctrl": 0.0, "mean": 0.1, "p50": 0.05, "p10": -0.5, "p90": 0.6, "std": 0.0}[stat]
    if stat == "std":
        return 0.5 + 0.01 * lead
    return float(base * wave + off)


def pass_frame(d: dt.date, bump: float = 0.0, with_gust: bool = True) -> pl.DataFrame:
    """Pasada 00Z sintética de `d`: 3 puntos × 13 pasos con todas las columnas `var_stat`."""
    init = dt.datetime.combine(d, dt.time(0))
    rows = []
    for p, (pid, _kind, _cap) in enumerate(POINTS):
        for lead in LEADS:
            r: dict[str, object] = {
                "init_time": init,
                "valid_time": init + dt.timedelta(hours=lead),
                "lead_hours": lead,
                "point_id": pid,
            }
            for v in (*INSTANT_VARS, "wind_gust_10m"):
                for s in STATS:
                    val = _value(v, s, p, lead, d.toordinal(), bump)
                    if v == "wind_gust_10m" and not with_gust:
                        val = float("nan")
                    r[f"{v}_{s}"] = np.float32(val)
            rows.append(r)
    df = pl.DataFrame(rows)
    return df.with_columns(pl.col("lead_hours").cast(pl.Int32))


@dataclass
class Synth:
    root: Path
    sources: Sources

    def write_pass(self, d: dt.date, **kw: object) -> None:
        p = self.root / "nwp" / f"init_date={d.isoformat()}"
        p.mkdir(parents=True, exist_ok=True)
        pass_frame(d, **kw).write_parquet(p / "part-0.parquet")  # pyright: ignore[reportArgumentType]

    def write_generation(self, scale: float = 1.0, hole_after: dt.datetime | None = None) -> None:
        ts = pl.datetime_range(
            dt.datetime(2025, 10, 20, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
            "15m",
            time_zone="UTC",
            time_unit="us",
            eager=True,
        )
        n = ts.len()
        on = (800.0 + 300.0 * np.sin(np.arange(n) / 50.0)) * scale
        off = (150.0 + 40.0 * np.cos(np.arange(n) / 30.0)) * scale
        df = pl.DataFrame(
            {
                "ts_utc": ts,
                "wind_offshore_mw": off,
                "wind_onshore_mw": on,
                "wind_mw": on + off,
                "is_gap_filled": np.zeros(n, bool),
            }
        )
        if hole_after is not None:
            df = df.filter(pl.col("ts_utc") < hole_after)
        df.write_delta(str(self.root / "generation"), mode="overwrite")

    def write_prices(self, shift_after: dt.datetime | None = None, shift: float = 0.0) -> None:
        """Precios sintéticos: horarios hasta 2025-12-31 y de 15 min después, con valores negativos.

        `first_seen_at` = 12:00 UTC de la víspera de la fecha UTC (los de antes del 2025-10-22
        quedan como primera descarga: censurados). `shift` se suma a las filas desde `shift_after`.
        """
        a, b = dt.datetime(2025, 10, 20, tzinfo=dt.UTC), dt.datetime(2026, 7, 14, tzinfo=dt.UTC)
        cut = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        first = dt.datetime(2025, 10, 21, 12, tzinfo=dt.UTC)
        hourly = pl.datetime_range(a, cut, "1h", time_zone="UTC", time_unit="us", eager=True)
        quarter = pl.datetime_range(
            cut, b, "15m", time_zone="UTC", time_unit="us", closed="right", eager=True
        )
        frames = [
            pl.DataFrame({"ts_utc": hourly[:-1]}).with_columns(resolution_min=pl.lit(60, pl.Int32)),
            pl.DataFrame({"ts_utc": quarter}).with_columns(resolution_min=pl.lit(15, pl.Int32)),
        ]
        df = pl.concat(frames)
        n = df.height
        price = 40.0 + 60.0 * np.sin(np.arange(n) / 37.0) + 25.0 * np.cos(np.arange(n) / 5.0)
        df = df.with_columns(
            price_eur_mwh=pl.Series(np.round(price, 2)),
            day=pl.col("ts_utc").dt.truncate("1d"),
        ).with_columns(
            fetched_at=pl.lit(first).cast(pl.Datetime("us", "UTC")),
            first_seen_at=pl.max_horizontal(
                pl.lit(first).cast(pl.Datetime("us", "UTC")),
                pl.col("day") - pl.duration(hours=12),
            ),
        )
        if shift_after is not None:
            df = df.with_columns(
                price_eur_mwh=pl.when(pl.col("ts_utc") >= shift_after)
                .then(pl.col("price_eur_mwh") + shift)
                .otherwise(pl.col("price_eur_mwh"))
            )
        df.drop("day").write_delta(str(self.root / "prices"), mode="overwrite")

    def write_operator_forecast(self) -> None:
        ts = pl.datetime_range(
            dt.datetime(2025, 10, 20, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
            "15m",
            time_zone="UTC",
            time_unit="us",
            eager=True,
        )
        pl.DataFrame({"ts_utc": ts, "forecast_mw": np.full(ts.len(), 777.0)}).write_delta(
            str(self.root / "forecast"), mode="overwrite"
        )


def _todas_las_subclases(cls: type[BaseSettings]) -> list[type[BaseSettings]]:
    return [s for sub in cls.__subclasses__() for s in (sub, *_todas_las_subclases(sub))]


@pytest.fixture(autouse=True)
def _aislar_ajustes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test lee el `.env` del equipo ni variables `PREDICO_*` del entorno."""
    for name in [n for n in os.environ if n.startswith("PREDICO_")]:
        monkeypatch.delenv(name)
    for cls in _todas_las_subclases(BaseSettings):
        monkeypatch.setitem(cls.model_config, "env_file", None)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


@pytest.fixture
def synth(tmp_path: Path) -> Synth:
    root = tmp_path
    cfg = {
        "points": [
            {"id": pid, "lat": 53.0, "lon": 12.0, "kind": kind, "capacity_mw": cap}
            for pid, kind, cap in POINTS
        ]
    }
    (root / "centroids.yaml").write_text(yaml.safe_dump(cfg))
    days = pl.date_range(dt.date(2025, 10, 1), CAP_LAST, "1d", eager=True)
    frames = []
    for pid, _kind, cap in POINTS:
        # La capacidad crece a escalones: cambia por fecha.
        mw = cap + 10.0 * ((days.dt.ordinal_day() + days.dt.year() * 366) // 20 % 7).cast(
            pl.Float64
        )
        frames.append(pl.DataFrame({"date": days, "cluster_id": pid, "capacity_mw": mw}))
    cc = pl.concat(frames).with_columns(n_units=pl.lit(10, pl.Int32))
    cc.write_delta(str(root / "cap_cluster"))
    kinds = pl.DataFrame({"cluster_id": [p[0] for p in POINTS], "kind": [p[1] for p in POINTS]})
    total = (
        cc.join(kinds, on="cluster_id")
        .group_by("date", "kind")
        .agg(capacity_mw=pl.col("capacity_mw").sum(), n_units=pl.col("n_units").sum())
        .sort("date", "kind")
    )
    total.write_delta(str(root / "cap_total"))
    s = Synth(
        root,
        Sources(
            nwp=str(root / "nwp"),
            generation=str(root / "generation"),
            cap_cluster=str(root / "cap_cluster"),
            cap_total=str(root / "cap_total"),
            centroids=str(root / "centroids.yaml"),
            prices=str(root / "prices"),
        ),
    )
    for d in DATES:
        s.write_pass(d)
    s.write_generation()
    s.write_prices()
    return s
