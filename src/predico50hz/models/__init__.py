"""Modelos del proyecto. `MODELS` es el registro que usa el CLI de backtest (`--model`)."""

from predico50hz.models.base import Model, Prediction
from predico50hz.models.baselines import Climatology, Operator, Persistence, PowerCurve
from predico50hz.models.lgbm import LightGBM, LightGBMCQR, LightGBMTuned, LightGBMTunedCQR

MODELS: dict[str, type[Model]] = {
    Climatology.name: Climatology,
    PowerCurve.name: PowerCurve,
    Operator.name: Operator,
    Persistence.name: Persistence,
    LightGBM.name: LightGBM,
    LightGBMCQR.name: LightGBMCQR,
    LightGBMTuned.name: LightGBMTuned,
    LightGBMTunedCQR.name: LightGBMTunedCQR,
}

__all__ = [
    "MODELS",
    "Climatology",
    "LightGBM",
    "LightGBMCQR",
    "LightGBMTuned",
    "LightGBMTunedCQR",
    "Model",
    "Operator",
    "Persistence",
    "PowerCurve",
    "Prediction",
]
