"""LightGBM: ni el ajuste ni la parada temprana ven filas del pliegue de validación."""

import datetime as dt
from pathlib import Path

import pytest

from backtest_synth import synth_gold, write_operator
from predico50hz.evaluation.backtest import run_backtest
from predico50hz.models.lgbm import LightGBM

FAST = {"max_rounds": 20, "early_stopping_rounds": 5, "min_data_in_leaf": 20, "learning_rate": 0.2}


def test_no_look_ahead_per_fold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[dt.date, dt.date, dt.date]] = []
    original = LightGBM.fit

    def spy(self: LightGBM, train) -> None:  # type: ignore[no-untyped-def]
        original(self, train)
        assert self.fit_dates is not None
        assert self.es_dates is not None
        seen.append((train["target_date"].max(), self.fit_dates[1], self.es_dates[1]))

    monkeypatch.setattr(LightGBM, "fit", spy)
    g = synth_gold()
    write_operator(tmp_path / "op", g)
    rec = run_backtest(
        "lgbm", root=tmp_path / "bt", operator=tmp_path / "op", table=(g, 1), model_params=FAST
    )
    assert len(seen) == len(rec.run["folds"]) > 1
    for (train_max, fit_max, es_max), info in zip(seen, rec.run["folds"], strict=True):
        valid_start = dt.date.fromisoformat(info["valid_start"])
        assert train_max < valid_start
        assert es_max < valid_start
        assert fit_max < es_max
