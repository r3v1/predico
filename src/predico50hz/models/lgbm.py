"""LightGBM v0.1 sin optimizar: Q50 (L2), Q10 y Q90 (pérdida cuantílica) sobre `y_cf`.

Tres modelos independientes con hiperparámetros razonables por defecto (sin Optuna todavía).
Trabaja en factor de capacidad (`unit = "cf"`); el marco lo pasa a MW con la capacidad del día,
ordena Q10 ≤ Q50 ≤ Q90 y recorta a [0, capacidad]. Aquí solo se recorta el cf a [0, 1] para no
devolver valores absurdos fuera del marco.

**Parada temprana sin look-ahead.** El conjunto de parada es el último tramo cronológico del
propio entrenamiento (los últimos `es_fraction` de los días, mínimo `es_min_days`, siempre días
completos). Nunca se usa el pliegue de validación: sería mirar al futuro. Es una decisión
simple: el modelo final no se reentrena con ese tramo, así pierde unos pocos días de datos a
cambio de un número de árboles honesto por cuantil.

**Variables.** Toda columna numérica que no sea meta (`features.registry.META`), objetivo
(`y_*`) ni disponibilidad (`available_at_*`). Los NaN (rachas ausentes) los trata LightGBM.
El bloque `price` (precio day-ahead de D, columnas `price_*`) está construido pero **excluido por
defecto** (`DEFAULT_EXCLUDE`): en validación mejoró solo un 0,6 % y no de forma clara
(`docs/f3-features.md` §9). Se activa con `exclude_prefixes=[]`.
"""

import datetime as dt
from pathlib import Path
from typing import Any, ClassVar

import lightgbm as lgb
import numpy as np
import polars as pl
import yaml

from predico50hz.features.registry import META, TARGETS
from predico50hz.models.base import Array, Model, Prediction
from predico50hz.models.cqr import MIN_DAYS, PUBLICATION_DELAY, WINDOW_DAYS, CQRConfig

QUANTILES: dict[str, float] = {"q10": 0.1, "q50": 0.5, "q90": 0.9}
EXCLUDED = {*META, *TARGETS}
# Bloques de variables fuera del modelo por defecto (ablación, ADR 0031 y #29).
DEFAULT_EXCLUDE: tuple[str, ...] = ("price_",)
# Hiperparámetros optimizados con Optuna (#32, ADR 0015): un conjunto por cuantil, versionado.
TUNED_PATH = Path(__file__).resolve().parents[3] / "config" / "lgbm_tuned.yaml"
# Claves que Optuna puede fijar por cuantil (el resto del modelo no cambia).
TUNABLE: tuple[str, ...] = (
    "num_leaves",
    "learning_rate",
    "min_data_in_leaf",
    "feature_fraction",
    "bagging_fraction",
    "lambda_l1",
    "lambda_l2",
)


def load_tuned_params(path: Path = TUNED_PATH) -> dict[str, dict[str, Any]]:
    """Hiperparámetros por cuantil (`q10`, `q50`, `q90`) de `config/lgbm_tuned.yaml`."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {q: dict(v) for q, v in data["quantiles"].items()}
    for q, hp in out.items():
        if q not in QUANTILES or not set(hp) <= set(TUNABLE):
            raise ValueError(f"{path}: parámetros no válidos para {q!r}: {sorted(hp)}")
    return out


def feature_columns(df: pl.DataFrame, exclude_prefixes: tuple[str, ...] = ()) -> list[str]:
    """Columnas de entrada: numéricas y que no sean meta, objetivo ni disponibilidad.

    `exclude_prefixes` quita además las columnas que empiecen así (para pruebas de ablación,
    p. ej. `("gust",)` deja fuera la racha, `gustfac_*` y `gust_missing`).
    """
    return [
        c
        for c, t in df.schema.items()
        if c not in EXCLUDED
        and not c.startswith(("y_", "available_at_", *exclude_prefixes))
        and (t.is_numeric() or t == pl.Boolean)
    ]


def split_early_stop(
    train: pl.DataFrame, fraction: float, min_days: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Parte el entrenamiento por días completos: (ajuste, parada temprana), cronológico."""
    days = train["target_date"].unique().sort()
    n_es = min(max(min_days, round(len(days) * fraction)), len(days) - 1)
    if n_es < 1:
        raise ValueError("entrenamiento con un solo día: no hay tramo de parada temprana")
    cut = days[len(days) - n_es]
    return train.filter(pl.col("target_date") < cut), train.filter(pl.col("target_date") >= cut)


def _matrix(df: pl.DataFrame, cols: list[str]) -> Array:
    return df.select(cols).cast(pl.Float64).to_numpy()


class LightGBM(Model):
    """Q50 con pérdida cuadrática; Q10 y Q90 con pérdida cuantílica; parada temprana."""

    name: ClassVar[str] = "lgbm"
    label: ClassVar[str] = "Modelo de árboles LightGBM (sin optimizar)"

    def __init__(  # noqa: PLR0913
        self,
        *,
        learning_rate: float = 0.03,
        num_leaves: int = 15,
        min_data_in_leaf: int = 200,
        feature_fraction: float = 0.5,
        bagging_fraction: float = 0.8,
        lambda_l1: float = 0.0,
        lambda_l2: float = 1.0,
        max_rounds: int = 1500,
        early_stopping_rounds: int = 50,
        es_fraction: float = 0.15,
        es_min_days: int = 7,
        seed: int = 42,
        exclude_prefixes: list[str] | None = None,
        quantile_params: dict[str, dict[str, Any]] | None = None,
        num_threads: int | None = None,
    ) -> None:
        # `quantile_params` pisa los hiperparámetros de un cuantil (Optuna da uno por cuantil).
        self.quantile_params: dict[str, dict[str, Any]] = quantile_params or {}
        self.num_threads = num_threads
        self.hp: dict[str, Any] = {
            "learning_rate": learning_rate,
            "num_leaves": num_leaves,
            "min_data_in_leaf": min_data_in_leaf,
            "feature_fraction": feature_fraction,
            "bagging_fraction": bagging_fraction,
            "lambda_l1": lambda_l1,
            "lambda_l2": lambda_l2,
            "max_rounds": max_rounds,
            "early_stopping_rounds": early_stopping_rounds,
            "es_fraction": es_fraction,
            "es_min_days": es_min_days,
            "seed": seed,
            "exclude_prefixes": list(
                DEFAULT_EXCLUDE if exclude_prefixes is None else exclude_prefixes
            ),
        }
        self._cols: list[str] = []
        self._boosters: dict[str, lgb.Booster] = {}
        self._info: list[dict[str, Any]] = []
        self._importance: list[dict[str, Any]] = []
        # Rango de fechas objetivo del tramo de parada temprana (para auditoría y tests).
        self.es_dates: tuple[Any, Any] | None = None
        self.fit_dates: tuple[Any, Any] | None = None

    def params(self) -> dict[str, Any]:
        return {
            **self.hp,
            "objectives": {"q50": "regression", "q10": "quantile", "q90": "quantile"},
            "alphas": {"q10": 0.1, "q90": 0.9},
            "quantile_params": self.quantile_params,
            "deterministic": True,
        }

    def _hp_for(self, q: str) -> dict[str, Any]:
        return {**self.hp, **self.quantile_params.get(q, {})}

    def _lgb_params(self, q: str, alpha: float) -> dict[str, Any]:
        hp = self._hp_for(q)
        p: dict[str, Any] = {
            "objective": "regression" if q == "q50" else "quantile",
            "metric": "l2" if q == "q50" else "quantile",
            "learning_rate": hp["learning_rate"],
            "num_leaves": hp["num_leaves"],
            "min_data_in_leaf": hp["min_data_in_leaf"],
            "feature_fraction": hp["feature_fraction"],
            "bagging_fraction": hp["bagging_fraction"],
            "bagging_freq": 1,
            "lambda_l1": hp["lambda_l1"],
            "lambda_l2": hp["lambda_l2"],
            "seed": hp["seed"],
            "deterministic": True,
            "force_row_wise": True,
            "verbosity": -1,
        }
        if q != "q50":
            p["alpha"] = alpha
        if self.num_threads is not None:
            p["num_threads"] = self.num_threads
        return p

    def fit(self, train: pl.DataFrame) -> None:
        t = train.drop_nulls("y_cf").sort("target_date", "ts_utc")
        self._cols = feature_columns(t, tuple(self.hp["exclude_prefixes"]))
        fit_df, es_df = split_early_stop(t, self.hp["es_fraction"], self.hp["es_min_days"])
        self.fit_dates = (fit_df["target_date"].min(), fit_df["target_date"].max())
        self.es_dates = (es_df["target_date"].min(), es_df["target_date"].max())
        x_fit, y_fit = _matrix(fit_df, self._cols), fit_df["y_cf"].to_numpy()
        x_es, y_es = _matrix(es_df, self._cols), es_df["y_cf"].to_numpy()
        self._boosters, self._info, self._importance = {}, [], []
        for q, alpha in QUANTILES.items():
            hp = self._hp_for(q)
            d_fit = lgb.Dataset(x_fit, y_fit, feature_name=self._cols)
            d_es = lgb.Dataset(x_es, y_es, reference=d_fit)
            booster = lgb.train(  # pyright: ignore[reportUnknownMemberType]
                self._lgb_params(q, alpha),
                d_fit,
                num_boost_round=hp["max_rounds"],
                valid_sets=[d_es],
                callbacks=[lgb.early_stopping(hp["early_stopping_rounds"], verbose=False)],
            )
            self._boosters[q] = booster
            self._info.append(
                {
                    "quantile": q,
                    "best_iteration": int(booster.best_iteration),
                    "n_fit_rows": fit_df.height,
                    "n_es_rows": es_df.height,
                    "es_start": self.es_dates[0],
                    "es_end": self.es_dates[1],
                }
            )
            gain = booster.feature_importance(importance_type="gain")
            self._importance += [
                {"quantile": q, "feature": f, "gain": float(g)}
                for f, g in zip(self._cols, gain, strict=True)
            ]

    def predict(self, df: pl.DataFrame) -> Prediction:
        x = _matrix(df, self._cols)
        out = {
            q: np.clip(
                np.asarray(b.predict(x, num_iteration=b.best_iteration), dtype=np.float64), 0.0, 1.0
            )
            for q, b in self._boosters.items()
        }
        return Prediction(q50=out["q50"], q10=out["q10"], q90=out["q90"])

    def artifacts(self) -> dict[str, pl.DataFrame]:
        """Importancia de variables (gain) y árboles/parada temprana por cuantil."""
        return {
            "feature_importance": pl.DataFrame(self._importance),
            "lgbm_early_stopping": pl.DataFrame(self._info),
        }


class LightGBMCQR(LightGBM):
    """LightGBM v0.1 más calibración conformal (CQR) de Q10 y Q90 sobre una ventana móvil.

    Es el mismo modelo (mismas predicciones brutas); el marco aplica después `models/cqr.py` con
    los residuos de las predicciones walk-forward anteriores, sin look-ahead. Solo sirve para
    la validación walk-forward: el periodo reservado necesitaría un historial de predicciones
    previo que esa ejecución no tiene.
    """

    name: ClassVar[str] = "lgbm_cqr"
    label: ClassVar[str] = "Modelo de árboles LightGBM con calibración conformal (CQR)"

    def __init__(
        self,
        *,
        cqr_window_days: int = WINDOW_DAYS,
        cqr_min_days: int = MIN_DAYS,
        cqr_publication_delay_hours: float = PUBLICATION_DELAY.total_seconds() / 3600.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._cqr = CQRConfig(
            window_days=cqr_window_days,
            min_days=cqr_min_days,
            publication_delay=dt.timedelta(hours=cqr_publication_delay_hours),
        )

    def cqr_config(self) -> CQRConfig:
        return self._cqr

    def params(self) -> dict[str, Any]:
        return {**super().params(), "cqr": self._cqr.as_dict()}


def _tuned_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Pone los hiperparámetros optimizados salvo que quien llama dé los suyos."""
    out = dict(kwargs)
    if out.get("quantile_params") is None:
        out["quantile_params"] = load_tuned_params()
    return out


class LightGBMTuned(LightGBM):
    """LightGBM con los hiperparámetros por cuantil de `config/lgbm_tuned.yaml` (#32)."""

    name: ClassVar[str] = "lgbm_tuned"
    label: ClassVar[str] = "Modelo de árboles LightGBM optimizado (Optuna)"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**_tuned_kwargs(kwargs))


class LightGBMTunedCQR(LightGBMCQR):
    """`lgbm_tuned` más calibración conformal (CQR), igual que `lgbm_cqr`."""

    name: ClassVar[str] = "lgbm_tuned_cqr"
    label: ClassVar[str] = "Modelo de árboles LightGBM optimizado con calibración conformal (CQR)"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**_tuned_kwargs(kwargs))
