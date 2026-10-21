# ruff: noqa: RUF002
"""Anti look-ahead de la CQR: la realidad del día objetivo (y de después) no entra en el ajuste."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from backtest_synth import synth_gold, write_operator
from predico50hz.evaluation.backtest import run_backtest
from predico50hz.models.cqr import CQRConfig, day_adjustments

D0 = dt.date(2026, 1, 1)
N_DAYS = 45
CFG = CQRConfig(window_days=20, min_days=5)
FAST = {
    "max_rounds": 20,
    "early_stopping_rounds": 5,
    "min_data_in_leaf": 20,
    "learning_rate": 0.2,
    "cqr_window_days": 20,
    "cqr_min_days": 5,
}


def _frame(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    recs: list[dict[str, object]] = []
    for i in range(N_DAYS):
        d = D0 + dt.timedelta(days=i)
        for r in range(8):
            q50 = float(rng.uniform(2000, 8000))
            recs.append(
                {
                    "forecast_issue_date": d,
                    "target_date": d + dt.timedelta(days=1),
                    "ts_utc": dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
                    + dt.timedelta(days=i, minutes=15 * r),
                    "y_mw": float(np.clip(q50 + rng.normal(0, 800), 0, 10_000)),
                    "cap_mw": 10_000.0,
                    "q10_mw": q50 - 700.0,
                    "q50_mw": q50,
                    "q90_mw": q50 + 700.0,
                }
            )
    return pl.DataFrame(recs)


def _qhat(df: pl.DataFrame, cfg: CQRConfig = CFG) -> dict[dt.date, float | None]:
    return dict(day_adjustments(df, cfg).select("forecast_issue_date", "qhat_cf").iter_rows())


def _alter(df: pl.DataFrame, mask: pl.Expr, value: float) -> pl.DataFrame:
    return df.with_columns(pl.when(mask).then(value).otherwise(pl.col("y_mw")).alias("y_mw"))


@pytest.mark.parametrize("day", [10, 20, 30, 44])
def test_adjustment_ignores_own_and_later_reality(day: int) -> None:
    """Para el cierre D la realidad del cierre D−1 (objetivo D) y posteriores no cuenta."""
    base = _frame()
    d = D0 + dt.timedelta(days=day)
    reference = _qhat(base)[d]
    assert reference is not None
    for value in (0.0, 10_000.0):
        altered = _alter(base, pl.col("forecast_issue_date") >= d - dt.timedelta(days=1), value)
        assert _qhat(altered)[d] == reference


def test_adjustment_does_depend_on_last_usable_day() -> None:
    """Control: el test anterior no es vacuo; cambiar los días utilizables sí mueve el Q̂."""
    base = _frame()
    d = D0 + dt.timedelta(days=30)
    altered = _alter(base, pl.col("forecast_issue_date") <= d - dt.timedelta(days=2), 0.0)
    assert _qhat(altered)[d] != _qhat(base)[d]


def test_publication_delay_moves_the_cutoff() -> None:
    """Con 16 h de retraso la realidad del cierre D−2 ya no llega antes del cierre de D."""
    base = _frame()
    d = D0 + dt.timedelta(days=30)
    slow = CQRConfig(window_days=20, min_days=5, publication_delay=dt.timedelta(hours=16))
    reference = _qhat(base, slow)[d]
    altered = _alter(base, pl.col("forecast_issue_date") >= d - dt.timedelta(days=2), 0.0)
    assert _qhat(altered, slow)[d] == reference


def test_backtest_intervals_unchanged_by_future_reality(tmp_path: Path) -> None:
    """De extremo a extremo: alterar la realidad desde T0 no cambia nada hasta T0 + 1."""
    gold = synth_gold()
    write_operator(tmp_path / "op", gold)
    t0 = dt.date(2026, 5, 15)

    def run(table: pl.DataFrame, name: str) -> pl.DataFrame:
        rec = run_backtest(
            "lgbm_cqr",
            root=tmp_path / name,
            operator=tmp_path / "op",
            table=(table, 1),
            model_params=FAST,
        )
        return rec.predictions.sort("ts_utc")

    altered = gold.with_columns(
        *[
            pl.when(pl.col("target_date") >= t0).then(v).otherwise(pl.col(c)).alias(c)
            for c, v in (("y_cf", 0.0), ("y_gen_mw", 0.0))
        ]
    )
    a, b = run(gold, "a"), run(altered, "b")
    cut = t0 + dt.timedelta(days=1)  # el objetivo T0 y T0+1 solo usan realidad hasta T0-1
    ka, kb = a.filter(pl.col("target_date") <= cut), b.filter(pl.col("target_date") <= cut)
    assert ka.height == kb.height > 0
    for col in ("q10_mw", "q50_mw", "q90_mw"):
        assert ka[col].to_list() == kb[col].to_list()
    # control: más adelante el Q̂ sí cambia (la realidad alterada ya es residuo utilizable)
    later_a = a.filter(pl.col("target_date") >= t0 + dt.timedelta(days=5))
    later_b = b.filter(pl.col("target_date") >= t0 + dt.timedelta(days=5))
    assert later_a.height == later_b.height
    assert later_a["q10_mw"].to_list() != later_b["q10_mw"].to_list()
