# ruff: noqa: PLR2004
"""Anti-fuga de los pliegues del backtest: entrenamiento previo, holdout fuera y sin solapes."""

import datetime as dt
from itertools import pairwise
from pathlib import Path

import polars as pl
import pytest

from backtest_synth import synth_gold
from predico50hz.evaluation import folds as fl

GOLD = Path(__file__).resolve().parents[2] / "data" / "gold" / "features"
MIN_MONTHS = [1, 2, 3, 4]


def _tables() -> list[pl.DataFrame]:
    out = [synth_gold()]
    if (GOLD / "_delta_log").is_dir():
        out.append(pl.read_delta(str(GOLD)).select("target_date", "split_v01"))
    return out


@pytest.mark.parametrize("min_months", MIN_MONTHS)
def test_training_strictly_before_validation(min_months: int) -> None:
    for df in _tables():
        for f in fl.dev_folds(df, min_months):
            train, valid = fl.split_fold(df, f)
            assert train.height > 0
            assert valid.height > 0
            assert train["target_date"].max() < f.valid_start
            assert train["target_date"].max() <= f.train_end
            assert valid["target_date"].min() >= f.valid_start
            assert valid["target_date"].max() <= f.valid_end


@pytest.mark.parametrize("min_months", MIN_MONTHS)
def test_holdout_never_in_folds(min_months: int) -> None:
    for df in _tables():
        holdout_dates = set(df.filter(pl.col("split_v01") == "holdout")["target_date"].to_list())
        assert holdout_dates
        folds = fl.dev_folds(df, min_months)
        assert max(f.valid_end for f in folds) < min(holdout_dates)
        for f in folds:
            train, valid = fl.split_fold(df, f)
            assert not (set(train["target_date"].to_list()) & holdout_dates)
            assert not (set(valid["target_date"].to_list()) & holdout_dates)
            assert set(train["split_v01"].unique()) <= {"train", "valid"}
            assert set(valid["split_v01"].unique()) <= {"train", "valid"}


@pytest.mark.parametrize("min_months", MIN_MONTHS)
def test_folds_do_not_overlap_and_grow(min_months: int) -> None:
    for df in _tables():
        folds = fl.dev_folds(df, min_months)
        assert len(folds) >= 2
        valids: list[set[dt.date]] = []
        sizes: list[int] = []
        for f in folds:
            train, valid = fl.split_fold(df, f)
            valids.append(set(valid["target_date"].to_list()))
            sizes.append(train.height)
        for i, a in enumerate(valids):
            for b in valids[i + 1 :]:
                assert not (a & b)
        assert sizes == sorted(sizes)  # ventana creciente
        assert len(set(sizes)) == len(sizes)
        for a, b in pairwise(folds):
            assert a.valid_end < b.valid_start


def test_min_train_months_respected() -> None:
    df = synth_gold()
    first = df["target_date"].min()
    assert isinstance(first, dt.date)
    for m in MIN_MONTHS:
        f0 = fl.dev_folds(df, m)[0]
        months = (f0.valid_start.year - first.year) * 12 + f0.valid_start.month - first.month
        assert months == m


@pytest.mark.data
def test_real_table_folds_cover_validation_and_train_months() -> None:
    if not (GOLD / "_delta_log").is_dir():
        pytest.skip("sin tabla oro")
    df = pl.read_delta(str(GOLD)).select("target_date", "split_v01")
    folds = fl.dev_folds(df, 3)
    assert folds[0].valid_start == dt.date(2025, 12, 1)
    assert folds[-1].valid_end == dt.date(2026, 7, 31)
    assert len(folds) == 8


def test_holdout_split_trains_on_dev_only() -> None:
    df = synth_gold()
    train, valid = fl.split_holdout(df)
    assert set(train["split_v01"].unique()) == {"train", "valid"}
    assert set(valid["split_v01"].unique()) == {"holdout"}
    assert train["target_date"].max() < valid["target_date"].min()
