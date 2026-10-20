"""Particiones del backtest: walk-forward con ventana creciente y pliegues mensuales (F4.1).

Todo se decide por `target_date` (el día del que se pronostica), no por `forecast_issue_date`.

- El conjunto de pliegues sale de las filas con `split_v01` ∈ {`train`, `valid`} (el periodo
  de desarrollo). El **periodo reservado** (`holdout`, F4.2) nunca entra en ellos.
- Pliegue `k` = un mes natural de validación; su entrenamiento son todas las filas del periodo
  de desarrollo con `target_date` **estrictamente anterior** al primer día del mes (ventana
  creciente).
- El primer pliegue empieza cuando hay al menos `min_train_months` meses naturales completos de
  entrenamiento previo (por defecto 3).
- La evaluación del periodo reservado es un único «pliegue» especial: entrena con todo el
  desarrollo y valida en el holdout (modo explícito `--holdout`, que queda registrado).
"""

import calendar
import datetime as dt
from dataclasses import dataclass

import polars as pl

DEV_SPLITS = ("train", "valid")
HOLDOUT_SPLIT = "holdout"
HOLDOUT_FOLD = -1
DEFAULT_MIN_TRAIN_MONTHS = 3


@dataclass(frozen=True)
class Fold:
    """Un pliegue: entrena con `target_date < valid_start`, valida en [valid_start, valid_end]."""

    index: int
    valid_start: dt.date
    valid_end: dt.date

    @property
    def train_end(self) -> dt.date:
        """Último `target_date` de entrenamiento (inclusivo)."""
        return self.valid_start - dt.timedelta(days=1)

    @property
    def label(self) -> str:
        return self.valid_start.strftime("%Y-%m")


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _add_months(d: dt.date, n: int) -> dt.date:
    total = d.year * 12 + (d.month - 1) + n
    return dt.date(total // 12, total % 12 + 1, 1)


def _month_end(d: dt.date) -> dt.date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def make_folds(
    first_date: dt.date, last_date: dt.date, min_train_months: int = DEFAULT_MIN_TRAIN_MONTHS
) -> list[Fold]:
    """Pliegues mensuales entre el primer y el último `target_date` del periodo de desarrollo.

    El primer pliegue empieza `min_train_months` meses naturales después del mes de
    `first_date`. El último puede ser el mes en curso incompleto (llega a `last_date`).
    """
    if min_train_months < 1:
        raise ValueError("min_train_months debe ser ≥ 1")
    start = _add_months(_month_start(first_date), min_train_months)
    folds: list[Fold] = []
    while start <= last_date:
        end = min(_month_end(start), last_date)
        folds.append(Fold(len(folds), start, end))
        start = _add_months(start, 1)
    return folds


def dev_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Filas del periodo de desarrollo (train + valid); nunca el holdout."""
    return df.filter(pl.col("split_v01").is_in(DEV_SPLITS))


def dev_folds(df: pl.DataFrame, min_train_months: int = DEFAULT_MIN_TRAIN_MONTHS) -> list[Fold]:
    """Pliegues del desarrollo a partir de la tabla oro (solo `target_date` de train + valid)."""
    dev = dev_frame(df)
    if dev.height == 0:
        return []
    first, last = dev["target_date"].min(), dev["target_date"].max()
    assert isinstance(first, dt.date)
    assert isinstance(last, dt.date)
    return make_folds(first, last, min_train_months)


def split_fold(df: pl.DataFrame, fold: Fold) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(entrenamiento, validación) del pliegue, siempre dentro del periodo de desarrollo."""
    dev = dev_frame(df)
    train = dev.filter(pl.col("target_date") < fold.valid_start)
    valid = dev.filter(pl.col("target_date").is_between(fold.valid_start, fold.valid_end))
    return train, valid


def split_holdout(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(todo el desarrollo, periodo reservado)."""
    return dev_frame(df), df.filter(pl.col("split_v01") == HOLDOUT_SPLIT)
