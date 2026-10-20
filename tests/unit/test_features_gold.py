# ruff: noqa: PLR2004
"""Objetivo, tabla oro sintética, escritura Delta idempotente y validación Pandera."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from deltalake import DeltaTable

from conftest import CAP_LAST, DATES, Synth
from predico50hz.features.build import build_gold, write_gold
from predico50hz.features.capacity import load_clusters
from predico50hz.features.registry import TARGETS, build_registry
from predico50hz.features.target import build_target
from predico50hz.features.validate import day_issues, schema_issues

UTC = pl.Datetime("us", "UTC")


def ts_frame(*stamps: dt.datetime) -> pl.DataFrame:
    return pl.DataFrame({"ts_utc": list(stamps)}, schema={"ts_utc": UTC})


def cap_total_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
            "cap_land_mw": [1000.0, 2000.0],
            "cap_sea_mw": [100.0, 200.0],
        }
    )


def test_target_cf_uses_capacity_of_the_interval_date() -> None:
    t1 = dt.datetime(2026, 1, 1, 23, 45, tzinfo=dt.UTC)  # fecha UTC 1 de enero
    t2 = dt.datetime(2026, 1, 2, 0, 0, tzinfo=dt.UTC)  # fecha UTC 2 de enero
    gen = ts_frame(t1, t2).with_columns(
        wind_mw=pl.Series([550.0, 550.0]),
        wind_onshore_mw=pl.Series([500.0, 500.0]),
        wind_offshore_mw=pl.Series([50.0, 50.0]),
        is_gap_filled=pl.Series([False, True]),
    )
    out = build_target(ts_frame(t1, t2), gen, cap_total_df())
    assert out["y_cap_mw"].to_list() == [1100.0, 2200.0]
    assert out["y_cf"].to_list() == pytest.approx([0.5, 0.25])
    assert out["y_cf_land"].to_list() == pytest.approx([0.5, 0.25])
    assert out["y_cf_sea"].to_list() == pytest.approx([0.5, 0.25])
    assert out["y_gen_mw"].to_list() == [550.0, 550.0]
    assert out["y_is_gap_filled"].to_list() == [False, True]
    assert out["capacity_ffilled"].to_list() == [False, False]


def test_target_ffills_capacity_and_nulls_without_generation() -> None:
    t = dt.datetime(2026, 1, 4, 12, 0, tzinfo=dt.UTC)  # después del último día de MaStR
    out = build_target(ts_frame(t), None, cap_total_df())
    assert out["capacity_ffilled"].to_list() == [True]
    assert out["y_cap_mw"].to_list() == [2200.0]  # último valor arrastrado
    assert out["y_gen_mw"].null_count() == 1
    assert out["y_cf"].null_count() == 1


def test_gold_synthetic_shape_and_steps(synth: Synth) -> None:
    df, info = build_gold(synth.sources)
    assert info["passes"] == len(DATES)
    steps = df.group_by("forecast_issue_date").len().sort("forecast_issue_date")
    assert steps["len"].to_list() == [100, 96, 92, 96]  # DATES: 2025-10-25, enero, 03-28, julio
    assert df.select(pl.struct("forecast_issue_date", "ts_utc").is_unique().all()).item()
    assert [c.name for c in build_registry(load_clusters(synth.sources.centroids))] == df.columns
    assert set(df["split_v01"].unique()) <= {"train", "valid", "holdout", "unused"}


def test_split_by_target_date(synth: Synth) -> None:
    df, _ = build_gold(synth.sources)
    by = df.group_by("target_date").agg(pl.col("split_v01").first()).sort("target_date")
    got = dict(zip(by["target_date"].to_list(), by["split_v01"].to_list(), strict=True))
    assert got[dt.date(2026, 1, 16)] == "train"
    assert got[dt.date(2026, 7, 11)] == "valid"


def test_capacity_ffilled_flag_after_last_mastr_day(synth: Synth) -> None:
    df, _ = build_gold(synth.sources, CAP_LAST, CAP_LAST)
    # Ventana 07-10 22:00 a 07-11 22:00 UTC: solo se arrastran las fechas UTC posteriores.
    pairs = set(
        zip(df["ts_utc"].dt.date().to_list(), df["capacity_ffilled"].to_list(), strict=True)
    )
    assert pairs == {(dt.date(2026, 7, 10), False), (dt.date(2026, 7, 11), True)}
    other, _ = build_gold(synth.sources, DATES[1], DATES[1])
    assert not other["capacity_ffilled"].any()


def test_target_nulls_only_where_generation_missing(synth: Synth) -> None:
    cut = dt.datetime(2026, 1, 16, 12, 0, tzinfo=dt.UTC)
    synth.write_generation(hole_after=cut)
    df, _ = build_gold(synth.sources, DATES[1], DATES[1])
    nulls = df.filter(pl.col("y_gen_mw").is_null())
    assert nulls.height > 0
    assert (nulls["ts_utc"] >= cut).all()
    assert df.filter(pl.col("ts_utc") < cut)["y_cf"].null_count() == 0
    assert df["y_cap_mw"].null_count() == 0
    assert set(TARGETS) <= set(df.columns)


def test_gold_has_no_nan_in_features_without_gust_missing(synth: Synth) -> None:
    df, _ = build_gold(synth.sources)
    feats = [
        c.name
        for c in build_registry(load_clusters(synth.sources.centroids))
        if c.role == "feature"
    ]
    assert (
        df.select(pl.any_horizontal(pl.col(feats).is_nan() | pl.col(feats).is_null()))
        .to_series()
        .sum()
        == 0
    )
    assert (df["gust_missing"] == 0).all()


def test_gold_marks_missing_gust(synth: Synth) -> None:
    synth.write_pass(DATES[1], with_gust=False)
    df, _ = build_gold(synth.sources, DATES[1], DATES[1])
    assert (df["gust_missing"] == 1).all()
    assert df["gust_all_mean"].is_nan().all()
    assert df["ws100_all_mean"].is_nan().sum() == 0
    clusters = load_clusters(synth.sources.centroids)
    assert schema_issues(build_registry(clusters), df) == []


def test_missing_pass_gives_no_rows_for_that_day(synth: Synth) -> None:
    (Path(synth.sources.nwp) / f"init_date={DATES[1]}" / "part-0.parquet").unlink()
    df, info = build_gold(synth.sources)
    assert info["passes"] == len(DATES) - 1
    assert DATES[1] not in set(df["forecast_issue_date"])


def test_write_gold_is_idempotent_and_replaces_ranges(synth: Synth, tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    df, _ = build_gold(synth.sources)
    assert write_gold(df, gold) is True
    assert DeltaTable(str(gold)).version() == 0
    assert write_gold(df, gold) is False  # mismo contenido: no crea versión
    assert DeltaTable(str(gold)).version() == 0
    # Rango: mismo contenido → nada; contenido cambiado → solo se reemplazan esas fechas.
    d = DATES[1]
    part, _ = build_gold(synth.sources, d, d)
    assert write_gold(part, gold, d, d) is False
    changed = part.with_columns(ws100_all_mean=pl.col("ws100_all_mean") + 1.0)
    assert write_gold(changed, gold, d, d) is True
    now = pl.read_delta(str(gold))
    assert now.height == df.height
    assert np.allclose(
        now.filter(pl.col("forecast_issue_date") == d).sort("ts_utc")["ws100_all_mean"],
        changed.sort("ts_utc")["ws100_all_mean"],
    )
    other = now.filter(pl.col("forecast_issue_date") != d).sort("forecast_issue_date", "ts_utc")
    ref = df.filter(pl.col("forecast_issue_date") != d).sort("forecast_issue_date", "ts_utc")
    assert other.equals(ref)
    assert write_gold(changed, gold, d, d) is False


def test_write_gold_idempotent_with_nan_columns(synth: Synth, tmp_path: Path) -> None:
    synth.write_pass(DATES[1], with_gust=False)
    df, _ = build_gold(synth.sources, DATES[1], DATES[1])
    assert write_gold(df, tmp_path / "g") is True
    assert write_gold(df, tmp_path / "g") is False


def test_validation_passes_on_synthetic_gold_and_detects_breakage(synth: Synth) -> None:
    clusters = load_clusters(synth.sources.centroids)
    reg = build_registry(clusters)
    df, _ = build_gold(synth.sources)
    assert schema_issues(reg, df) == []
    assert day_issues(df) == []
    bad_cf = df.with_columns(
        y_cf=pl.when(pl.int_range(pl.len()) == 5).then(2.0).otherwise(pl.col("y_cf"))
    )
    assert any(i["column"] == "y_cf" for i in schema_issues(reg, bad_cf))
    bad_nan = df.with_columns(
        ws100_all_mean=pl.when(pl.int_range(pl.len()) == 7)
        .then(float("nan"))
        .otherwise(pl.col("ws100_all_mean"))
    )
    assert any(i["column"] == "ws100_all_mean" for i in schema_issues(reg, bad_nan))
    dup = pl.concat([df, df.head(1)])
    assert schema_issues(reg, dup)
    hole = df.filter(pl.int_range(pl.len()) != 10)
    assert schema_issues(reg, hole)
    assert day_issues(hole)
    late = df.with_columns(available_at_nwp=pl.col("closure_utc"))
    assert any(i["check"] == "available_before_closure" for i in schema_issues(reg, late))
