"""Optimización de hiperparámetros de LightGBM con Optuna (#32, ADR 0015).

**Un estudio por cuantil** (`q50`, `q10`, `q90`), todos en el mismo SQLite (`data/optuna/`).

**Objetivo.** Media, sobre los pliegues de optimización, de una métrica en MW:

- `q50`: RMSE del Q50 (la métrica de la puerta G1).
- `q10`, `q90`: pérdida cuantílica (pinball) del propio cuantil. Se prefiere al Winkler tras CQR
  porque cada estudio queda independiente (el Winkler necesita Q10 y Q90 a la vez, y la CQR
  encadena pliegues con residuos previos) y porque la pérdida cuantílica es la que entrena el
  modelo y mide cada cuantil por separado. La calibración conformal corrige después la
  cobertura; el Winkler con CQR se mide al final, en la evaluación completa.

**Sin look-ahead.** Cada pliegue entrena con `target_date` anterior a su mes (ventana creciente,
`evaluation.folds`) y el número de árboles sale de la parada temprana del propio entrenamiento
(`models.lgbm.split_early_stop`), nunca del mes de validación. Solo se leen pliegues del
periodo de desarrollo: el periodo reservado no entra (`fold_tasks` lo comprueba).

**Presupuesto.** Cada intento entrena un solo árbol por pliegue con 1 hilo, y se lanzan varios
procesos (`workers`) sobre el mismo SQLite: con ~90 000 filas, un hilo por proceso aprovecha
mucho mejor los núcleos que muchos hilos en un solo modelo. La poda por mediana corta los
intentos malos tras unos pocos pliegues. Los pliegues de optimización pueden ser un
subconjunto (`even`, pliegues alternos) para guardar los demás como comprobación aparte.

**Semilla.** TPE con semilla `seed + nº de proceso` (si todos usaran la misma, los intentos
aleatorios iniciales se repetirían). El resultado depende del reparto de intentos entre
procesos, así que es reproducible con un solo proceso y, con varios, sólo en su conjunto.
"""

import datetime as dt
import multiprocessing as mp
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import optuna
import polars as pl
import yaml

from predico50hz.evaluation import folds as fl
from predico50hz.features.config import DEFAULT_GOLD
from predico50hz.models.base import Array
from predico50hz.models.lgbm import (
    DEFAULT_EXCLUDE,
    QUANTILES,
    TUNABLE,
    LightGBM,
    _matrix,  # pyright: ignore[reportPrivateUsage]
    feature_columns,
    split_early_stop,
)

DEFAULT_DB = "data/optuna/predico.db"
STUDY_PREFIX = "lgbm"
SEED = 42
N_STARTUP = 20  # intentos aleatorios de TPE y mínimo de intentos completos antes de podar
WARMUP_FOLDS = 2  # no se poda antes de haber evaluado este nº de pliegues
MAX_ROUNDS = 2000
BASELINE_WAIT_S = 600.0  # espera máxima de los procesos hasta que el 0 tome la v0.1
EXPERIMENT = "predico50hz-optuna"
FOLD_SETS = ("all", "even", "odd")
# Intento 0 de cada estudio: la v0.1 sin optimizar, para medirla con el mismo criterio.
BASELINE: dict[str, float | int] = {
    "num_leaves": 15,
    "learning_rate": 0.03,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.8,
    "lambda_l1": 1e-3,
    "lambda_l2": 1.0,
}


def study_name(quantile: str, prefix: str = STUDY_PREFIX) -> str:
    return f"{prefix}-{quantile}"


# Espacio ampliado, solo exploratorio: el óptimo del ADR cae junto a los bordes del espacio.
WIDE_LEAVES_MIN = 4
WIDE_FEATURE_FRACTION_MIN = 0.2


def suggest(trial: optuna.Trial, space: str = "adr") -> dict[str, float | int]:
    """Espacio de búsqueda. `adr` es el exacto del ADR 0015; `wide` amplía hojas y variables."""
    wide = space == "wide"
    if space not in {"adr", "wide"}:
        raise ValueError(f"espacio desconocido {space!r}")
    return {
        "num_leaves": trial.suggest_int("num_leaves", WIDE_LEAVES_MIN if wide else 15, 255),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 20, 500),
        "feature_fraction": trial.suggest_float(
            "feature_fraction", WIDE_FEATURE_FRACTION_MIN if wide else 0.5, 1.0
        ),
        "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
        "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10.0, log=True),
        "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10.0, log=True),
    }


@dataclass(frozen=True)
class FoldTask:
    """Un pliegue de optimización: entrenamiento y validación (siempre del desarrollo)."""

    fold: fl.Fold
    train: pl.DataFrame
    valid: pl.DataFrame


def select_folds(folds: list[fl.Fold], which: str) -> list[fl.Fold]:
    """`all`, `even` (índices pares), `odd` (impares) o índices separados por comas."""
    if which == "all":
        return list(folds)
    if which == "even":
        return [f for f in folds if f.index % 2 == 0]
    if which == "odd":
        return [f for f in folds if f.index % 2 == 1]
    wanted = {int(x) for x in which.split(",")}
    chosen = [f for f in folds if f.index in wanted]
    if len(chosen) != len(wanted):
        raise ValueError(f"pliegues inexistentes en {which!r}")
    return chosen


def fold_tasks(
    df: pl.DataFrame,
    which: str = "all",
    min_train_months: int = fl.DEFAULT_MIN_TRAIN_MONTHS,
) -> list[FoldTask]:
    """Pliegues de la optimización. Falla si algo del periodo reservado se coló."""
    tasks: list[FoldTask] = []
    for f in select_folds(fl.dev_folds(df, min_train_months), which):
        train, valid = fl.split_fold(df, f)
        train, valid = train.drop_nulls("y_cf"), valid.drop_nulls(["y_gen_mw", "y_cf"])
        for part in (train, valid):
            if fl.HOLDOUT_SPLIT in set(part["split_v01"].unique().to_list()):
                raise AssertionError("el periodo reservado ha entrado en la optimización")
        if train.height and valid.height:
            tasks.append(FoldTask(f, train, valid))
    if not tasks:
        raise ValueError("sin pliegues evaluables")
    return tasks


def metric_mw(quantile: str, y_mw: Array, pred_mw: Array) -> float:
    """RMSE (`q50`) o pérdida cuantílica media (`q10`, `q90`) en MW."""
    if quantile == "q50":
        return float(np.sqrt(np.mean((pred_mw - y_mw) ** 2)))
    tau = QUANTILES[quantile]
    d = y_mw - pred_mw
    return float(np.mean(np.maximum(tau * d, (tau - 1.0) * d)))


def score_fold(  # noqa: PLR0913
    task: FoldTask,
    quantile: str,
    hp: dict[str, Any],
    *,
    max_rounds: int = MAX_ROUNDS,
    early_stopping_rounds: int = 50,
    exclude_prefixes: tuple[str, ...] = DEFAULT_EXCLUDE,
    num_threads: int = 1,
) -> tuple[float, int]:
    """Entrena un solo cuantil en el pliegue y devuelve (métrica en MW, árboles usados)."""
    model = LightGBM(
        max_rounds=max_rounds,
        early_stopping_rounds=early_stopping_rounds,
        quantile_params={quantile: hp},
        num_threads=num_threads,
    )
    train = task.train.sort("target_date", "ts_utc")
    cols = feature_columns(train, exclude_prefixes)
    fit_df, es_df = split_early_stop(train, model.hp["es_fraction"], model.hp["es_min_days"])
    d_fit = lgb.Dataset(_matrix(fit_df, cols), fit_df["y_cf"].to_numpy(), feature_name=cols)
    d_es = lgb.Dataset(_matrix(es_df, cols), es_df["y_cf"].to_numpy(), reference=d_fit)
    booster = lgb.train(  # pyright: ignore[reportUnknownMemberType]
        model._lgb_params(quantile, QUANTILES[quantile]),  # pyright: ignore[reportPrivateUsage]
        d_fit,
        num_boost_round=max_rounds,
        valid_sets=[d_es],
        callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
    )
    x = _matrix(task.valid, cols)
    pred_cf = np.clip(
        np.asarray(booster.predict(x, num_iteration=booster.best_iteration), dtype=np.float64),
        0.0,
        1.0,
    )
    cap = task.valid["y_cap_mw"].to_numpy().astype(np.float64)
    y = task.valid["y_gen_mw"].to_numpy().astype(np.float64)
    return metric_mw(quantile, y, np.clip(pred_cf * cap, 0.0, cap)), int(booster.best_iteration)


def make_objective(
    tasks: list[FoldTask], quantile: str, space: str = "adr", **score_kwargs: Any
) -> Callable[[optuna.Trial], float]:
    """Objetivo de Optuna: media de la métrica por pliegue, con informe por pliegue (poda)."""

    def objective(trial: optuna.Trial) -> float:
        hp = suggest(trial, space)
        t0 = time.monotonic()
        values: list[float] = []
        trees: list[int] = []
        for step, task in enumerate(tasks):
            value, best_iter = score_fold(task, quantile, hp, **score_kwargs)
            values.append(value)
            trees.append(best_iter)
            trial.report(float(np.mean(values)), step)
            if trial.should_prune():
                trial.set_user_attr("fold_values", values)
                trial.set_user_attr("fold_trees", trees)
                trial.set_user_attr("folds_done", len(values))
                trial.set_user_attr("seconds", round(time.monotonic() - t0, 1))
                raise optuna.TrialPruned
        trial.set_user_attr("fold_values", values)
        trial.set_user_attr("fold_trees", trees)
        trial.set_user_attr("folds_done", len(values))
        trial.set_user_attr("seconds", round(time.monotonic() - t0, 1))
        return float(np.mean(values))

    return objective


def make_pruner() -> optuna.pruners.MedianPruner:
    return optuna.pruners.MedianPruner(n_startup_trials=N_STARTUP, n_warmup_steps=WARMUP_FOLDS)


def make_sampler(seed: int) -> optuna.samplers.TPESampler:
    return optuna.samplers.TPESampler(
        seed=seed, n_startup_trials=N_STARTUP, multivariate=True, constant_liar=True
    )


def storage_url(db: str | Path) -> str:
    return f"sqlite:///{db}"


def make_storage(db: str | Path) -> optuna.storages.RDBStorage:
    """SQLite con tiempo de espera largo y modo WAL: varios procesos escriben a la vez."""
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA journal_mode=WAL")
    return optuna.storages.RDBStorage(
        storage_url(path), engine_kwargs={"connect_args": {"timeout": 300}}
    )


def create_study(db: str | Path, quantile: str, prefix: str = STUDY_PREFIX) -> optuna.Study:
    """Crea (o abre) el estudio del cuantil; si es nuevo, encola la v0.1 como intento 0."""
    study = optuna.create_study(
        study_name=study_name(quantile, prefix),
        storage=make_storage(db),
        sampler=make_sampler(SEED),
        pruner=make_pruner(),
        direction="minimize",
        load_if_exists=True,
    )
    if not study.trials:
        study.enqueue_trial(dict(BASELINE))
    return study


def _worker(  # noqa: PLR0913, PLR0917
    worker_id: int,
    n_trials: int,
    db: str,
    quantile: str,
    prefix: str,
    gold: str,
    folds: str,
    min_train_months: int,
    num_threads: int,
    space: str,
    baseline_started: Any,
    mlflow_uri: str | None,
    parent_id: str | None,
) -> None:
    """Proceso de Optuna: carga la tabla oro una vez y ejecuta `n_trials` intentos."""
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    df = pl.read_delta(gold)
    tasks = fold_tasks(df, folds, min_train_months)
    del df
    study = optuna.load_study(
        study_name=study_name(quantile, prefix),
        storage=make_storage(db),
        sampler=make_sampler(SEED + worker_id),
        pruner=make_pruner(),
    )
    objective = make_objective(tasks, quantile, space, num_threads=num_threads)
    if worker_id != 0:
        # Si dos procesos sacan a la vez el intento encolado, ambos entrenan la v0.1 y uno falla
        # al cerrarlo: solo el proceso 0 lo toma y los demás esperan a que haya empezado.
        baseline_started.wait(BASELINE_WAIT_S)
    callbacks = (
        [MlflowTrialCallback(mlflow_uri, parent_id, study.study_name)]
        if mlflow_uri and parent_id
        else []
    )
    study.optimize(
        _announcing(objective, baseline_started, worker_id), n_trials=n_trials, callbacks=callbacks
    )


def _announcing(
    objective: Callable[[optuna.Trial], float], started: Any, worker_id: int
) -> Callable[[optuna.Trial], float]:
    """El proceso 0 avisa de que ya empezó su primer intento (el de la v0.1)."""

    def run(trial: optuna.Trial) -> float:
        if worker_id == 0:
            started.set()
        return objective(trial)

    return run


def _counts(study: optuna.Study) -> dict[str, int]:
    out = {s.name: 0 for s in optuna.trial.TrialState}
    for t in study.get_trials(deepcopy=False):
        out[t.state.name] += 1
    return out


def run_study(  # noqa: PLR0913
    quantile: str,
    trials: int,
    *,
    workers: int,
    db: str | Path = DEFAULT_DB,
    prefix: str = STUDY_PREFIX,
    gold: str | Path = DEFAULT_GOLD,
    folds: str = "even",
    min_train_months: int = fl.DEFAULT_MIN_TRAIN_MONTHS,
    num_threads: int = 1,
    space: str = "adr",
    mlflow_uri: str | None = None,
    poll_s: float = 60.0,
    log: Callable[[str], None] = print,
) -> optuna.Study:
    """Lanza `workers` procesos que reparten `trials` intentos nuevos y espera a que acaben."""
    if quantile not in QUANTILES:
        raise ValueError(f"cuantil desconocido {quantile!r}")
    study = create_study(db, quantile, prefix)
    workers = max(1, min(workers, trials))
    shares = [trials // workers + (1 if i < trials % workers else 0) for i in range(workers)]
    ctx = mp.get_context("spawn")  # polars y LightGBM no toleran bien `fork`
    parent_id = (
        start_parent(mlflow_uri, study.study_name, {"folds": folds, "space": space})
        if mlflow_uri
        else None
    )
    baseline_started = ctx.Event()
    if not any(t.state == optuna.trial.TrialState.WAITING for t in study.trials):
        baseline_started.set()  # nada encolado (estudio ya empezado): nadie espera
    procs = [
        ctx.Process(
            target=_worker,
            args=(
                i,
                n,
                str(db),
                quantile,
                prefix,
                str(gold),
                folds,
                min_train_months,
                num_threads,
                space,
                baseline_started,
                mlflow_uri,
                parent_id,
            ),
        )
        for i, n in enumerate(shares)
    ]
    t0 = time.monotonic()
    for p in procs:
        p.start()
    while any(p.is_alive() for p in procs):
        time.sleep(poll_s)
        c = _counts(study)
        best = study.best_value if c["COMPLETE"] else float("nan")
        log(
            f"[{quantile}] {(time.monotonic() - t0) / 60:6.1f} min  completos {c['COMPLETE']}  "
            f"podados {c['PRUNED']}  en curso {c['RUNNING']}  mejor {best:,.2f} MW"
        )
    for p in procs:
        p.join()
    failed = any(p.exitcode != 0 for p in procs)
    if mlflow_uri and parent_id:
        finish_parent(study, mlflow_uri, parent_id, "FAILED" if failed else "FINISHED")
    if failed:
        raise RuntimeError(f"algún proceso falló: códigos {[p.exitcode for p in procs]}")
    return study


def importances(study: optuna.Study) -> dict[str, float]:
    """Importancia de cada hiperparámetro (PED-ANOVA: no necesita scikit-learn)."""
    from optuna.importance import (  # noqa: PLC0415
        PedAnovaImportanceEvaluator,
        get_param_importances,
    )

    return dict(get_param_importances(study, evaluator=PedAnovaImportanceEvaluator()))


def summarize_study(study: optuna.Study) -> dict[str, Any]:
    """Resumen del estudio: intentos por estado, mejor intento y su importancia."""
    best = study.best_trial
    return {
        "study": study.study_name,
        "counts": _counts(study),
        "best_value": best.value,
        "best_number": best.number,
        "best_params": dict(best.params),
        "best_fold_values": best.user_attrs.get("fold_values"),
        "best_fold_trees": best.user_attrs.get("fold_trees"),
        "baseline_value": study.trials[0].value,
        "importance": importances(study),
        "seconds_total": sum(t.user_attrs.get("seconds", 0.0) for t in study.trials),
    }


def _client(uri: str) -> tuple[Any, str]:
    """Cliente de MLflow y id del experimento de Optuna (se crea si no existe)."""
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    from mlflow import MlflowClient  # noqa: PLC0415

    client = MlflowClient(tracking_uri=uri)
    exp = client.get_experiment_by_name(EXPERIMENT)
    return client, exp.experiment_id if exp else str(client.create_experiment(EXPERIMENT))


def start_parent(uri: str, name: str, tags: dict[str, str] | None = None) -> str:
    """Run padre de un estudio (queda en curso hasta `finish_parent`)."""
    client, exp_id = _client(uri)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    run = client.create_run(
        exp_id, run_name=f"{name}-{stamp}", tags={"study": name, "kind": "optuna", **(tags or {})}
    )
    return str(run.info.run_id)


def log_trial(client: Any, exp_id: str, parent_id: str, study: str, t: Any) -> None:
    """Run hijo de un intento terminado: parámetros, métrica por pliegue (step), estado, árboles."""
    from mlflow.entities import Metric, Param, RunTag  # noqa: PLC0415

    now = int(time.time() * 1000)
    start = int(t.datetime_start.timestamp() * 1000) if t.datetime_start else now
    run = client.create_run(
        exp_id,
        start_time=start,
        run_name=f"trial-{t.number:03d}",
        tags={"mlflow.parentRunId": parent_id, "study": study, "kind": "optuna-trial"},
    )
    rid = run.info.run_id
    folds: list[float] = t.user_attrs.get("fold_values") or []
    trees: list[int] = t.user_attrs.get("fold_trees") or []
    metrics = [Metric("fold_value_mw", float(v), now, i) for i, v in enumerate(folds)]
    metrics += [Metric("fold_trees", float(v), now, i) for i, v in enumerate(trees)]
    metrics += [
        Metric("running_mean_mw", float(v), now, int(k)) for k, v in t.intermediate_values.items()
    ]
    for key in ("folds_done", "seconds"):
        if t.user_attrs.get(key) is not None:
            metrics.append(Metric(key, float(t.user_attrs[key]), now, 0))
    if t.value is not None:
        metrics.append(Metric("value_mw", float(t.value), now, 0))
    client.log_batch(
        rid,
        metrics=metrics,
        params=[Param(k, str(v)) for k, v in t.params.items()],
        tags=[RunTag("trial_state", t.state.name), RunTag("trial_number", str(t.number))],
    )
    client.set_terminated(rid, "FINISHED", end_time=now)


class MlflowTrialCallback:
    """Callback de Optuna: registra cada intento al terminar, sin frenar el estudio."""

    def __init__(self, uri: str, parent_id: str, study: str) -> None:
        self.uri, self.parent_id, self.study = uri, parent_id, study

    def __call__(self, study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        del study
        try:
            client, exp_id = _client(self.uri)
            log_trial(client, exp_id, self.parent_id, self.study, trial)
        except Exception as e:  # el registro nunca debe parar la optimización
            print(f"AVISO: no se pudo registrar el intento {trial.number} en MLflow: {e}")


def finish_parent(study: optuna.Study, uri: str, parent_id: str, status: str = "FINISHED") -> None:
    """Cierra el run padre con el resumen del estudio (mejor intento, cuentas, importancia)."""
    from mlflow.entities import Metric, Param  # noqa: PLC0415

    client, _ = _client(uri)
    now = int(time.time() * 1000)
    if status == "FINISHED":
        s = summarize_study(study)
        metrics = {
            "best_value_mw": s["best_value"],
            "baseline_value_mw": s["baseline_value"],
            "best_number": s["best_number"],
            "n_complete": s["counts"]["COMPLETE"],
            "n_pruned": s["counts"]["PRUNED"],
            "n_fail": s["counts"]["FAIL"],
            "seconds_total": s["seconds_total"],
            **{f"importance_{k}": v for k, v in s["importance"].items()},
        }
        client.log_batch(
            parent_id,
            metrics=[Metric(k, float(v), now, 0) for k, v in metrics.items()]
            + [
                Metric("best_fold_value_mw", float(v), now, i)
                for i, v in enumerate(s["best_fold_values"] or [])
            ],
            params=[Param(f"best_{k}", str(v)) for k, v in s["best_params"].items()],
        )
    client.set_terminated(parent_id, status, end_time=now)


def log_mlflow(study: optuna.Study, uri: str, extra_tags: dict[str, str] | None = None) -> str:
    """Vuelca en MLflow un estudio ya terminado: run padre con un run hijo por intento."""
    parent = start_parent(uri, study.study_name, {"backfill": "true", **(extra_tags or {})})
    client, exp_id = _client(uri)
    for t in study.get_trials(deepcopy=False):
        if t.state.is_finished():
            log_trial(client, exp_id, parent, study.study_name, t)
    finish_parent(study, uri, parent)
    return parent


def export_params(
    db: str | Path,
    out: str | Path,
    *,
    prefix: str = STUDY_PREFIX,
    quantiles: tuple[str, ...] = ("q10", "q50", "q90"),
    note: str = "",
) -> dict[str, dict[str, float | int]]:
    """Escribe el mejor intento de cada estudio en `config/lgbm_tuned.yaml`."""
    best: dict[str, dict[str, float | int]] = {}
    meta: dict[str, Any] = {}
    storage = make_storage(db)
    for q in quantiles:
        study = optuna.load_study(study_name=study_name(q, prefix), storage=storage)
        params = {k: study.best_params[k] for k in TUNABLE}
        best[q] = {
            k: int(v) if k in {"num_leaves", "min_data_in_leaf"} else float(v)
            for k, v in params.items()
        }
        meta[q] = {
            "study": study.study_name,
            "trial": study.best_trial.number,
            "objective_mw": round(float(study.best_value), 3),
            "baseline_mw": round(float(study.trials[0].value or float("nan")), 3),
        }
    doc = {"note": note, "studies": meta, "quantiles": best}
    header = (
        "# Hiperparámetros de LightGBM optimizados con Optuna (#32, ADR 0015), uno por cuantil.\n"
        "# Los lee `models.lgbm.load_tuned_params` (modelos `lgbm_tuned` y `lgbm_tuned_cqr`).\n"
        "# Se regeneran con `python -m predico50hz.evaluation tune-export`.\n"
    )
    Path(out).write_text(
        header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return best
