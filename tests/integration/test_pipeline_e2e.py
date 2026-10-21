# ruff: noqa: PLR2004, RUF003
"""Cadena entera con datos sintéticos: ingesta → plata → oro → entrenamiento → métricas.

Las etapas están en `chain_stages.py` y los datos sintéticos en `chain_synth.py`. Sin red, sin
`data/` ni `/mnt/data`. Ver `docs/f4-backtest.md` (§10).
"""

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from chain_stages import run_chain
from chain_synth import MIN_TRAIN_MONTHS, ONSHORE, Chain, issue_dates, wind_truth
from deltalake import DeltaTable

from predico50hz.evaluation import scorecard
from predico50hz.evaluation.backtest import KEY, run_backtest
from predico50hz.evaluation.store import RunRecord, holdout_count, load_runs
from predico50hz.evaluation.validate import validate_predictions
from predico50hz.features import timing
from predico50hz.features.capacity import load_clusters
from predico50hz.features.registry import build_registry
from predico50hz.features.validate import day_issues, schema_issues
from predico50hz.ingest.dynamical.bronze import BronzeStore
from predico50hz.ingest.dynamical.silver import silver_path
from predico50hz.ingest.dynamical.validate import validate_silver
from predico50hz.ingest.ifs.extract import load_points
from predico50hz.ingest.mastr import validate as mastr_validate
from predico50hz.models import MODELS

pytestmark = pytest.mark.integration

LGBM_PARAMS = {
    "min_data_in_leaf": 20,
    "learning_rate": 0.1,
    "max_rounds": 300,
    "num_leaves": 7,
    "es_min_days": 4,
}
METRIC_KEYS = {
    "n_rows", "rmse", "mae", "bias", "winkler", "coverage", "mean_width", "rmse_operator",
    "n_days", "peak_abs_error_mw", "peak_bias_mw", "peak_shift_h",
    "days_better_than_operator_pct", "n_days_ranked",
}  # fmt: skip


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Chain:
    """Las etapas de ingesta y la tabla oro se ejecutan una sola vez por módulo."""
    return run_chain(tmp_path_factory.mktemp("cadena"))


# --------------------------------------------------------------------------------------
# Etapa 1: MaStR → plata de capacidad y centroides
# --------------------------------------------------------------------------------------
def test_mastr_export_to_silver_and_centroids(chain: Chain) -> None:
    rep = chain.mastr_report
    assert rep["schema_ok"], rep["schema_issues"]
    assert rep["units_in_zone"] == chain.n_units_zone
    assert rep["units_without_cluster_in_zone"] == 0
    assert rep["clusters"]["k_land"] == 4
    assert rep["clusters"]["n_sea"] == 2  # dos parques a más de media celda NWP
    assert rep["clusters"]["centroids_outside_domain"] == []
    # La zona se decide por Regelzone o por Bundesland: Niedersachsen y TenneT quedan fuera.
    assert rep["zone"]["bundesland_rule_only_would_add"]["n"] == 1  # el de TenneT en Brandenburg

    pts = load_points(chain.centroids)
    assert len(pts) == 6
    assert sorted(p.kind for p in pts) == ["land"] * 4 + ["sea"] * 2

    silver = chain.silver / "mastr"
    cluster = pl.read_delta(str(silver / "capacity_cluster"))
    total = pl.read_delta(str(silver / "capacity_total"))
    assert not mastr_validate.schema_issues(mastr_validate.capacity_schema("cluster_id"), cluster)
    assert not mastr_validate.schema_issues(mastr_validate.capacity_schema("kind"), total)
    assert set(cluster["cluster_id"]) == {p.id for p in pts}
    # Los clústeres suman exactamente el total de cada tipo y día.
    kinds = pl.DataFrame({"cluster_id": [p.id for p in pts], "kind": [p.kind for p in pts]})
    by_kind = (
        cluster.join(kinds, on="cluster_id")
        .group_by("date", "kind")
        .agg(pl.col("capacity_mw").sum())
        .join(total, on=["date", "kind"], suffix="_total")
    )
    diff = (by_kind["capacity_mw"] - by_kind["capacity_mw_total"]).abs().max()
    assert isinstance(diff, float)
    assert diff < 1e-6
    # La capacidad terrestre sube con las altas de 2026 y baja con las dos bajas del 12-may.
    land = total.filter(pl.col("kind") == "land").sort("date")
    mw = dict(zip(land["date"], land["capacity_mw"], strict=True))
    assert mw[dt.date(2026, 5, 12)] - mw[dt.date(2026, 5, 11)] == pytest.approx(-10.0)
    assert mw[dt.date(2026, 7, 31)] > mw[dt.date(2026, 3, 31)]


# --------------------------------------------------------------------------------------
# Etapa 2: NWP (origen falso) → bronce Icechunk → plata Parquet por clúster
# --------------------------------------------------------------------------------------
def test_nwp_bronze_to_silver(chain: Chain) -> None:
    days = issue_dates()
    assert [m.init_date for m in chain.nwp_metrics] == [d.isoformat() for d in days]
    assert {m.status for m in chain.nwp_metrics} == {"ok"}
    assert all(m.n_nan_fraction == 0.0 for m in chain.nwp_metrics)
    store = BronzeStore(chain.nwp_bronze)
    assert all(store.is_ingested(d) for d in (days[0], days[-1]))
    assert not store.is_ingested(days[0] + dt.timedelta(days=1))
    assert store.digest(days[3]) == chain.nwp_metrics[3].bronze_digest
    ds = store.read_pass(days[0])
    assert ds.attrs["provenance"][days[0].isoformat()]["source_snapshot_id"].startswith("SNAP-")
    assert bool(np.all(np.diff(ds["latitude"].values) > 0))  # bronce con latitud ascendente

    pts = load_points(chain.centroids)
    frames: list[pl.DataFrame] = []
    checked = {*days[::8], days[-1]}
    for d in days:
        df = pl.read_parquet(silver_path(chain.nwp_silver, d.isoformat()))
        if d in checked:  # Pandera es lento: se valida una muestra de pasadas
            issues = validate_silver(df, d, len(pts))  # esquema Pandera de la plata del NWP
            assert not issues, (d, issues)
        frames.append(df)
    allp = pl.concat(frames)
    # La plata conserva la señal: el viento del clúster sigue al viento verdadero…
    valid = allp["valid_time"].to_numpy().astype("datetime64[ns]")
    corr = np.corrcoef(allp["wind_speed_100m_mean"].to_numpy(), wind_truth(valid))[0, 1]
    assert corr > 0.9  # sesgo por pasada, dispersión de miembros y gradiente espacial
    # …y la latitud no quedó invertida: el mar (más al norte) sopla más que la tierra.
    mean = dict(allp.group_by("point_id").agg(pl.col("wind_speed_100m_mean").mean()).iter_rows())
    sea = [v for k, v in mean.items() if k.startswith("sea_")]
    land = [v for k, v in mean.items() if not k.startswith("sea_")]
    assert min(sea) > max(land)


# --------------------------------------------------------------------------------------
# Etapa 3: ENTSO-E (API falsa) → bronce XML → plata de generación y pronóstico del operador
# --------------------------------------------------------------------------------------
def test_entsoe_bronze_to_silver(chain: Chain) -> None:
    for name, infos in chain.entsoe_info.items():
        assert all(i["errors"] == [] for i in infos), name
        assert all(i["chunks_nodata"] == 0 for i in infos), name
    assert chain.entsoe_info["generation"][1]["chunks_written"] == 12  # 6 meses × 2 psrType
    bronze = chain.root / "bronze" / "entsoe" / "generation"
    assert sorted(p.name for p in bronze.iterdir()) == [
        "fetched_at=20260802T000000Z",
        "fetched_at=20260803T000000Z",
    ]
    assert list((bronze / "fetched_at=20260803T000000Z").glob("*.xml.gz"))

    for name in ("generation", "forecast"):
        rep = chain.entsoe_reports[name]
        assert rep["schema_ok"], rep["schema_issues"]
    gen = pl.read_delta(str(chain.silver / "entsoe" / "generation"))
    assert gen["ts_utc"].diff().drop_nulls().unique().to_list() == [dt.timedelta(minutes=15)]
    assert not gen["is_gap_filled"].any()
    assert gen["wind_mw"].to_list() == pytest.approx(
        (gen["wind_onshore_mw"] + gen["wind_offshore_mw"]).to_list()
    )
    # Para cada clave gana la descarga más reciente: julio sale entero de la segunda.
    assert chain.fake is not None
    july = gen.filter(pl.col("ts_utc").dt.month() == 7)
    lo = int(np.searchsorted(chain.fake.ts, np.datetime64("2026-07-01", "ns")))
    expected = chain.fake._gen[ONSHORE][lo : lo + july.height]
    assert july["wind_onshore_mw"].to_numpy() == pytest.approx(expected)

    fc = pl.read_delta(str(chain.silver / "entsoe" / "forecast"))
    assert set(fc["psr_type"]) == {"B18", "B19"}
    assert fc["forecast_mw"].min() >= 0


# --------------------------------------------------------------------------------------
# Etapa 4: tabla oro
# --------------------------------------------------------------------------------------
def test_gold_table(chain: Chain) -> None:
    assert chain.gold_rc == 0  # `python -m predico50hz.features`: construye y valida
    rep = json.loads((chain.validation / "gold_features.json").read_text())
    assert rep["schema_ok"], rep["problems"]
    days = issue_dates()
    assert rep["days"] == len(days)
    assert rep["days_by_steps"] == {"92": 1, "96": len(days) - 1}  # 28-mar: cambio de hora

    gold = pl.read_delta(str(chain.gold))
    clusters = load_clusters(chain.centroids)
    assert schema_issues(build_registry(clusters), gold) == []  # esquema Pandera de la oro
    assert day_issues(gold) == []
    assert gold.select(KEY).is_duplicated().sum() == 0
    assert set(gold["split_v01"]) == {"train", "valid", "holdout"}
    assert gold["y_cf"].null_count() == 0
    assert gold["y_cf"].min() >= 0
    assert gold["y_cf"].max() <= 1.0
    # El objetivo sale de la plata de ENTSO-E y la capacidad de la de MaStR.
    gen = pl.read_delta(str(chain.silver / "entsoe" / "generation"))
    cap = pl.read_delta(str(chain.silver / "mastr" / "capacity_total"))
    cap_day = cap.group_by("date").agg(pl.col("capacity_mw").sum().alias("cap"))
    j = (
        gold.with_columns(date=pl.col("ts_utc").dt.date())
        .join(gen.select("ts_utc", "wind_mw"), on="ts_utc")
        .join(cap_day, on="date")
    )
    assert j.height == gold.height
    assert (j["y_gen_mw"] - j["wind_mw"]).abs().max() < 1e-9
    assert (j["y_cap_mw"] - j["cap"]).abs().max() < 1e-6
    # La variable principal explica la generación (relación sintética viento → potencia).
    assert gold.select(pl.corr("wshub_all_mean", "y_cf")).item() > 0.85
    # Cada día usa su pasada 00Z y su malla de Berlín.
    first = gold.filter(pl.col("forecast_issue_date") == days[0])
    assert first["ts_utc"].to_list() == timing.target_grid(days[0])


# --------------------------------------------------------------------------------------
# Etapa 5: backtest walk-forward, modelos y métricas
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def runs(chain: Chain) -> dict[str, RunRecord]:
    op = chain.silver / "entsoe" / "forecast"
    out: dict[str, RunRecord] = {}
    for name in ("operator", "climatology", "power_curve", "lgbm", "lgbm_cqr"):
        params = {"lgbm": LGBM_PARAMS, "lgbm_cqr": {**LGBM_PARAMS, "cqr_min_days": 3}}.get(name)
        out[name] = run_backtest(
            name,
            gold=chain.gold,
            root=chain.backtest,
            operator=op,
            min_train_months=MIN_TRAIN_MONTHS,
            model_params=params,
        )
    out["lgbm_holdout"] = run_backtest(
        "lgbm",
        holdout=True,
        gold=chain.gold,
        root=chain.backtest,
        operator=op,
        min_train_months=MIN_TRAIN_MONTHS,
        model_params=LGBM_PARAMS,
    )
    return out


def _all_finite(d: dict[str, Any], skip: set[str]) -> bool:
    return all(
        isinstance(v, int | float) and math.isfinite(v) for k, v in d.items() if k not in skip
    )


def test_backtest_folds_and_valid_predictions(chain: Chain, runs: dict[str, RunRecord]) -> None:
    gold = pl.read_delta(str(chain.gold))
    dev = gold.filter(pl.col("split_v01").is_in(["train", "valid"]))
    expected = dev.filter(pl.col("target_date") >= dt.date(2026, 5, 1)).select(KEY)
    rec = runs["lgbm"]
    assert [f["label"] for f in rec.run["folds"]] == ["2026-05", "2026-06", "2026-07"]
    n_train = [f["n_train_rows"] for f in rec.run["folds"]]
    assert n_train == sorted(n_train)  # ventana creciente
    assert rec.run["gold"]["delta_version"] == DeltaTable(str(chain.gold)).version()
    for name in ("operator", "climatology", "power_curve", "lgbm", "lgbm_cqr"):
        preds = runs[name].predictions
        validate_predictions(preds, expected)  # esquema Pandera + malla completa
        assert preds.height == expected.height
        assert preds["target_date"].max() <= dt.date(2026, 7, 31)  # el holdout no entra
        assert (Path(runs[name].directory) / "predictions.parquet").is_file()
    # CQR: mismo Q50 que lgbm y algún día calibrado con los residuos anteriores
    cqr = runs["lgbm_cqr"]
    assert cqr.run["cqr"]["n_days_calibrated"] > 0
    assert cqr.predictions["q50_mw"].equals(runs["lgbm"].predictions["q50_mw"])
    assert (Path(cqr.directory) / "cqr_adjustment.parquet").is_file()
    assert (Path(rec.directory) / "feature_importance.parquet").is_file()
    ho = runs["lgbm_holdout"]
    assert set(ho.predictions["target_date"]) == {dt.date(2026, 8, 1)}
    assert set(ho.predictions["fold"]) == {-1}
    validate_predictions(ho.predictions)
    assert holdout_count(chain.backtest, "lgbm") == 1
    assert holdout_count(chain.backtest) == 1


def test_backtest_metrics_are_complete_and_reasonable(runs: dict[str, RunRecord]) -> None:
    for name in ("operator", "climatology", "power_curve", "lgbm", "lgbm_holdout"):
        met = runs[name].metrics
        assert set(met) == {"total", "by_fold", "by_month"}
        assert set(met["total"]) == METRIC_KEYS
        no_interval = {"winkler", "coverage", "mean_width", "days_better_than_operator_pct"}
        assert _all_finite(met["total"], no_interval if name == "operator" else set()), name
        assert met["total"]["rmse"] > 0
        assert met["total"]["rmse_operator"] > 0
    assert len(runs["lgbm"].metrics["by_fold"]) == 3
    assert [m["month"] for m in runs["lgbm"].metrics["by_month"]] == [
        "2026-05", "2026-06", "2026-07"
    ]  # fmt: skip
    # El operador no da intervalo: sus métricas de intervalo son nulas, nunca inventadas.
    assert runs["operator"].metrics["total"]["coverage"] is None
    for name in ("climatology", "power_curve", "lgbm"):
        cov = runs[name].metrics["total"]["coverage"]
        assert 0.2 < cov < 1.0, (name, cov)

    rmse = {n: runs[n].metrics["total"]["rmse"] for n in ("climatology", "power_curve", "lgbm")}
    # Con una señal viento → generación clara, los modelos que ven el NWP ganan de largo a la
    # climatología (que solo conoce la hora y el mes), y LightGBM no puede quedarse a medias.
    assert rmse["lgbm"] < 0.6 * rmse["climatology"], rmse
    assert rmse["power_curve"] < 0.6 * rmse["climatology"], rmse
    cap = runs["lgbm"].predictions["cap_mw"].max()
    assert rmse["lgbm"] < 0.2 * cap
    # El Q50 de LightGBM aprende del viento: la variable más importante es de viento.
    imp = pl.read_parquet(Path(runs["lgbm"].directory) / "feature_importance.parquet")
    top = (
        imp.filter(pl.col("quantile") == "q50")
        .group_by("feature")
        .agg(pl.col("gain").sum())
        .sort("gain", descending=True)["feature"][0]
    )
    assert re.match(r"^(ws|wshub|v3|pdens|u100|v100|gust)", top), top


def test_scorecard_and_leaderboard_over_all_runs(chain: Chain, runs: dict[str, RunRecord]) -> None:
    assert {r["model"] for r in load_runs(chain.backtest)} == set(MODELS)
    lb = pl.read_parquet(chain.backtest / "leaderboard.parquet")
    assert lb.height == len(runs)
    card = scorecard.build_scorecard(chain.backtest)
    by = {m["model"]: m for m in card["models"]}
    assert set(by) == set(MODELS)
    lgbm = by["lgbm"]
    assert lgbm["validation"]["rmse"] == pytest.approx(runs["lgbm"].metrics["total"]["rmse"])
    assert lgbm["validation"]["verdict"] in {"mejora", "igual", "empeora"}
    assert lgbm["g1"]["status"] in {"cumple", "no cumple"}
    assert lgbm["holdout"] is not None
    assert by["climatology"]["holdout"] is None
    assert card["holdout_evaluations_total"] == 1
    assert card["headline"]["text"]
    json.dumps(card, allow_nan=False)  # serializable y sin NaN
