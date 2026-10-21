"""Interfaz común de modelo para el marco de backtest (F4) y para F5/F6.

Un modelo solo implementa `fit` y `predict`. El marco (`evaluation/backtest.py`) se ocupa de lo
demás: pliegues, conversión a MW con `y_cap_mw`, orden Q10 ≤ Q50 ≤ Q90, recorte a
[0, capacidad], validación y registro. Para enchufar un modelo nuevo:

1. Subclasee `Model`, ponga `name` y devuelva sus parámetros en `params()`.
2. `fit(train)` recibe filas de la tabla oro (features, meta y objetivos `y_*`).
3. `predict(df)` recibe filas **sin ninguna columna `y_*`** (el marco las quita) y devuelve una
   `Prediction` con `q50` (obligatorio) y, si el modelo da intervalo, `q10` y `q90`.
4. Registre la clase en `predico50hz.models.MODELS`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import numpy as np
import numpy.typing as npt
import polars as pl

Array = npt.NDArray[np.float64]
Unit = Literal["cf", "mw"]


@dataclass(frozen=True)
class Prediction:
    """Cuantiles por fila de entrada, en la unidad del modelo (`Model.unit`).

    `q10` y `q90` son opcionales, pero van juntos: un modelo sin intervalo (p. ej. el operador)
    deja ambos en `None` y sus métricas de intervalo quedan nulas.
    """

    q50: Array
    q10: Array | None = None
    q90: Array | None = None

    def __post_init__(self) -> None:
        if (self.q10 is None) != (self.q90 is None):
            raise ValueError("q10 y q90 deben darse juntos o ninguno")
        n = len(self.q50)
        for q in (self.q10, self.q90):
            if q is not None and len(q) != n:
                raise ValueError("los cuantiles deben tener la misma longitud")


class Model(ABC):
    """Modelo de pronóstico. `unit` es `cf` (factor de capacidad) o `mw`."""

    name: ClassVar[str]
    # Nombre en lenguaje llano para el informe de negocio (por defecto, `name`).
    label: ClassVar[str] = ""
    unit: ClassVar[Unit] = "cf"
    # Referencia externa que no se entrena (el operador): no se compara consigo misma.
    is_operator: ClassVar[bool] = False

    def params(self) -> dict[str, Any]:
        """Parámetros serializables (JSON) que se guardan en `run.json`."""
        return {}

    @abstractmethod
    def fit(self, train: pl.DataFrame) -> None:
        """Entrena con filas de la tabla oro (con `y_cf`, `y_gen_mw`...)."""

    def artifacts(self) -> dict[str, pl.DataFrame]:
        """Tablas opcionales de la última `fit` (p. ej. importancia de variables).

        El marco las junta por pliegue (columna `fold`) y las guarda como
        `<nombre>.parquet` en el directorio de la ejecución.
        """
        return {}

    @abstractmethod
    def predict(self, df: pl.DataFrame) -> Prediction:
        """Pronostica una fila por fila de `df` (sin columnas `y_*`)."""
