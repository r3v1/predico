"""Modelos del proyecto. `MODELS` es el registro que usa el CLI de backtest (`--model`)."""

from predico50hz.models.base import Model, Prediction
from predico50hz.models.baselines import Climatology, Operator, PowerCurve

MODELS: dict[str, type[Model]] = {
    Climatology.name: Climatology,
    PowerCurve.name: PowerCurve,
    Operator.name: Operator,
}

__all__ = ["MODELS", "Climatology", "Model", "Operator", "PowerCurve", "Prediction"]
