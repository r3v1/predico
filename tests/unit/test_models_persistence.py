# ruff: noqa: PLR2004
"""Persistencia: corte de publicación, sin mirar al futuro, modos y cuantiles (datos sintéticos)."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from predico50hz.features.timing import closure_utc, target_grid
from predico50hz.models import MODELS
from predico50hz.models.baselines import Persistence

STEP = dt.timedelta(minutes=15)
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
LAG = dt.timedelta(minutes=180)


def _gen_value(ts: dt.datetime) -> float:
    """Generación sintética única por cuarto de hora: 100 + índice de 15 min desde START."""
    return 100.0 + (ts - START) // STEP


def write_gen(path: Path, end: dt.datetime, gap_filled: set[dt.datetime] | None = None) -> None:
    ts = [START + i * STEP for i in range((end - START) // STEP + 1)]
    pl.DataFrame(
        {
            "ts_utc": ts,
            "wind_mw": [_gen_value(t) for t in ts],
            "is_gap_filled": [t in (gap_filled or set()) for t in ts],
        }
    ).with_columns(pl.col("ts_utc").dt.cast_time_unit("us")).write_delta(str(path))


def frame(days: list[dt.date]) -> pl.DataFrame:
    rows = [
        {"forecast_issue_date": d, "ts_utc": t, "y_gen_mw": _gen_value(t)}
        for d in days
        for t in target_grid(d)
    ]
    return pl.DataFrame(rows).with_columns(pl.col("ts_utc").dt.cast_time_unit("us"))


def strip(df: pl.DataFrame) -> pl.DataFrame:
    return df.drop([c for c in df.columns if c.startswith("y_")])


def test_registered_as_reference() -> None:
    assert MODELS["persistence"] is Persistence
    assert Persistence.is_reference
    assert Persistence().params()["lag_min"] == 180


@pytest.mark.parametrize(
    ("day", "closure_hour"), [(dt.date(2026, 7, 10), 13), (dt.date(2026, 1, 15), 14)]
)
def test_cutoff_respects_dst_and_is_inclusive(
    tmp_path: Path, day: dt.date, closure_hour: int
) -> None:
    closure = closure_utc(day)
    assert closure.hour == closure_hour  # 13:00 UTC en verano, 14:00 en invierno
    cut = closure - LAG
    write_gen(tmp_path / "g", closure + dt.timedelta(days=2))
    m = Persistence(mode="last", source=tmp_path / "g")
    p = m.predict(strip(frame([day])))
    # el cuarto de hora que empieza justo en el corte se usa; el siguiente (+15 min) no
    assert np.all(p.q50 == _gen_value(cut))
    assert _gen_value(cut + STEP) not in set(p.q50)


def test_no_lookahead_after_cutoff(tmp_path: Path) -> None:
    days = [dt.date(2026, 3, 1) + dt.timedelta(days=i) for i in range(6)]
    d = days[-1]
    cut = closure_utc(d) - LAG
    end = closure_utc(d) + dt.timedelta(days=2)
    write_gen(tmp_path / "a", end)
    write_gen(tmp_path / "b", end)
    # b: la generación posterior al corte cambia por completo (y los huecos rellenados también)
    b = pl.read_delta(str(tmp_path / "b"))
    b = b.with_columns(
        pl.when(pl.col("ts_utc") > cut).then(-999.0).otherwise(pl.col("wind_mw")).alias("wind_mw")
    )
    b.write_delta(str(tmp_path / "b"), mode="overwrite")
    for mode in ("profile", "last"):
        outs = []
        for src in ("a", "b"):
            m = Persistence(mode=mode, source=tmp_path / src)  # type: ignore[arg-type]
            m.fit(frame(days[:-1]))
            outs.append(m.predict(strip(frame([d]))))
        assert np.array_equal(outs[0].q50, outs[1].q50)
        assert outs[0].q10 is not None
        assert np.array_equal(outs[0].q10, outs[1].q10)
        assert np.array_equal(outs[0].q90, outs[1].q90)
        assert (outs[0].q50 >= 0).all()


def test_profile_and_last_modes(tmp_path: Path) -> None:
    d = dt.date(2026, 3, 10)
    cut = closure_utc(d) - LAG
    write_gen(tmp_path / "g", closure_utc(d) + dt.timedelta(days=2))
    df = strip(frame([d]))
    ts = df["ts_utc"].to_list()
    last = Persistence(mode="last", source=tmp_path / "g").predict(df).q50
    assert np.all(last == _gen_value(cut))
    prof = Persistence(mode="profile", source=tmp_path / "g").predict(df).q50
    for t, v in zip(ts, prof, strict=True):
        k = 1
        while t - dt.timedelta(days=k) > cut:
            k += 1
        assert v == _gen_value(t - dt.timedelta(days=k))
    # el día objetivo cubre más de 24 h desde el corte: se usan las dos jornadas anteriores
    assert {(t - cut) > dt.timedelta(days=1) for t in ts} == {True, False}


def test_gap_filled_and_missing_data(tmp_path: Path) -> None:
    d = dt.date(2026, 3, 10)
    cut = closure_utc(d) - LAG
    # el último cuarto de hora utilizable está relleno: se descarta y se usa el anterior
    write_gen(tmp_path / "g", closure_utc(d) + dt.timedelta(days=2), gap_filled={cut})
    df = strip(frame([d]))
    p = Persistence(mode="last", source=tmp_path / "g").predict(df)
    assert np.all(p.q50 == _gen_value(cut - STEP))
    # sin datos recientes (más allá de max_stale_min) la fila sale NaN
    write_gen(tmp_path / "h", cut - dt.timedelta(hours=8))
    stale = Persistence(mode="last", source=tmp_path / "h").predict(df)
    assert np.isnan(stale.q50).all()


def test_quantiles_ordered_and_framework(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    days = [dt.date(2026, 2, 1) + dt.timedelta(days=i) for i in range(40)]
    ts = [START + i * STEP for i in range(((closure_utc(days[-1]) - START) // STEP) + 2 * 96)]
    pl.DataFrame(
        {
            "ts_utc": ts,
            "wind_mw": 3000 + 1500 * np.sin(np.arange(len(ts)) / 40) + rng.normal(0, 100, len(ts)),
            "is_gap_filled": False,
        }
    ).with_columns(pl.col("ts_utc").dt.cast_time_unit("us")).write_delta(str(tmp_path / "g"))
    gen = pl.read_delta(str(tmp_path / "g"))
    truth = (
        frame(days)
        .join(gen.select("ts_utc", "wind_mw"), on="ts_utc")
        .with_columns(y_gen_mw=pl.col("wind_mw"))
    )
    m = Persistence(source=tmp_path / "g", min_count=50)
    m.fit(truth.drop("wind_mw"))
    p = m.predict(strip(truth.drop("wind_mw")))
    assert p.q10 is not None
    assert p.q90 is not None
    assert (p.q10 <= p.q50).all()
    assert (p.q50 <= p.q90).all()
    assert (p.q90 - p.q10 > 0).all()
