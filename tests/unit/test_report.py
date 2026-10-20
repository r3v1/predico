# ruff: noqa: PLR2004
"""Pruebas sin red del informe ejecutivo con datos sintéticos."""

import datetime as dt
import re
import shutil
from pathlib import Path
from typing import Any

import icechunk
import numpy as np
import polars as pl
import pytest
import yaml
import zarr

from predico50hz.ingest.dynamical.bronze import init_pos, n_init
from predico50hz.ingest.dynamical.config import LEAD_HOURS, N_MEMBERS, VARIABLES
from predico50hz.report import forecast, plan, production, quality, zone
from predico50hz.report.build import _variables, build, build_data, fill, fmt_es
from predico50hz.report.paths import Paths

REPO = Path(__file__).resolve().parents[2]
GH_MILESTONES = [
    {"number": 2, "title": "2 · Modelo v0.1", "due_on": "2026-10-29T00:00:00Z",
     "open_issues": 2, "closed_issues": 0, "state": "open"},
    {"number": 1, "title": "1 · G0 · Archivo diario estable", "due_on": "2026-10-22T00:00:00Z",
     "open_issues": 1, "closed_issues": 2, "state": "open"},
    {"number": 9, "title": "9 · Después del 1 dic", "due_on": None,
     "open_issues": 1, "closed_issues": 0, "state": "open"},
]  # fmt: skip


def _gh_issue(n: int, title: str, labels: list[str], ms: int, state: str = "open", **kw: Any):
    return {
        "number": n,
        "title": title,
        "state": state,
        "labels": [{"name": x} for x in labels],
        "milestone": {"number": ms},
        "body": f"texto\n<!-- backlog:{kw.pop('mark', 'x')} -->",
        **kw,
    }


GH_ISSUES = [
    _gh_issue(1, "G0: 7 días seguidos", ["tipo:puerta"], 1, mark="g0"),
    _gh_issue(23, "F1.4 · Etapa 3", ["tipo:epic"], 2, mark="f1.4",
              sub_issues_summary={"total": 3, "completed": 1, "percent_completed": 33}),
    _gh_issue(4, "Operación diaria", ["tipo:epic"], 1, "closed", mark="ops",
              sub_issues_summary={"total": 3, "completed": 3, "percent_completed": 100}),
    _gh_issue(5, "Epic vacía", ["tipo:epic"], 1, sub_issues_summary={"total": 0, "completed": 0}),
    {"number": 6, "title": "PR", "state": "open", "labels": [], "pull_request": {}},
]  # fmt: skip


def _fake_gh(endpoint: str) -> list[dict[str, Any]]:
    return GH_MILESTONES if "/milestones" in endpoint else GH_ISSUES


def _boom(endpoint: str) -> list[dict[str, Any]]:
    raise OSError("sin red")


NOW = dt.datetime(2026, 10, 20, 12, tzinfo=dt.UTC)
LABELS = yaml.safe_load((REPO / "report" / "labels.yaml").read_text("utf-8"))


def test_plan_from_github_milestones_gates_and_blocks(tmp_path: Path) -> None:
    cache = tmp_path / "data" / "report" / "github_plan.json"
    raw = plan.load(cache, NOW, fetch=_fake_gh)
    p = plan.build_plan(raw, LABELS, NOW.date())
    assert p is not None
    assert p["source"] == {"kind": "github", "fetched": "2026-10-20T12:00:00+00:00"}
    ms = p["milestones"]
    assert [m["date"] for m in ms] == ["2026-10-17", "2026-10-22", "2026-10-29", "2026-12-01"]
    assert [m["status"] for m in ms] == ["done", "progress", "pending", "pending"]
    assert ms[1]["gate"] == "G0"
    assert (ms[1]["closed"], ms[1]["total"]) == (2, 3)
    assert [g["gate"] for g in p["gates"]] == ["G0"]
    assert p["days_to_league"] == 42
    assert p["rules"] == LABELS["rules"]
    blocks = {b["title"]: b for b in p["blocks"]}  # la épica sin sub-issues y el PR no cuentan
    assert set(blocks) == {"Histórico meteorológico completo", "Operación diaria de la descarga"}
    b = blocks["Histórico meteorológico completo"]
    assert (b["id"], b["status"], b["done"], b["pending"]) == ("F1.4", "progress", 1, 2)
    assert blocks["Operación diaria de la descarga"]["status"] == "done"
    assert {s["id"]: s["status"] for s in p["stages"]} == {
        "0": "done", "1": "done", "2": "done", "3": "progress",
    }  # fmt: skip
    assert cache.is_file()


def test_plan_falls_back_to_cache_then_none(tmp_path: Path) -> None:
    cache = tmp_path / "github_plan.json"
    assert plan.load(cache, NOW, fetch=_boom) is None
    assert plan.build_plan(None, LABELS, NOW.date()) is None
    plan.load(cache, NOW, fetch=_fake_gh)
    later = NOW + dt.timedelta(days=1)
    for kw in ({"fetch": _boom}, {"offline": True, "fetch": _boom}):
        raw = plan.load(cache, later, **kw)
        assert raw is not None
        assert raw["source"] == "cache"
        assert raw["fetched"] == "2026-10-20T12:00:00+00:00"
    p = plan.build_plan(raw, LABELS, later.date())
    assert p["source"]["kind"] == "cache"


def test_cluster_names() -> None:
    assert zone.cluster_name(
        "bb_01", "land", "Tierra, Brandenburg, Landkreis dominante: Prignitz"
    ) == (
        "Prignitz",
        "Prignitz (Brandenburg)",
    )
    short, long = zone.cluster_name("s", "sea", "Mar (Báltico); parques: Baltic 1, OWP Windanker")
    assert short == "Baltic 1"
    assert long == "Baltic 1 + Windanker"


def test_number_format_and_fill() -> None:
    assert fmt_es(10940) == "10.940"
    assert fmt_es(25.46, 1) == "25,5"
    assert fill("{a} y {b}", {"a": "1"}) == "1 y {b}"


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_delta(str(path))


def _synthetic_root(root: Path) -> None:
    shutil.copytree(
        REPO / "report", root / "report", ignore=shutil.ignore_patterns("index.html", "*.json")
    )
    silver = root / "data" / "silver"
    units = pl.DataFrame(
        {
            "in_zone": [True, True, True, False],
            "status": ["In Betrieb", "In Betrieb", "Vorübergehend stillgelegt", "In Betrieb"],
            "kind": ["land", "land", "sea", "land"],
            "lon": [12.0, 12.001, 14.0, 9.0],
            "lat": [53.0, 53.001, 54.8, 50.0],
            "net_mw": [3.0, 2.0, 5.0, 4.0],
            "hub_height_m": [120.0, None, 100.0, 90.0],
            "cluster_id": ["bb_01", "bb_01", "sea_a", None],
        }
    )
    _write(units, silver / "mastr" / "units")
    clusters = pl.DataFrame(
        {
            "cluster_id": ["bb_01", "sea_a"],
            "kind": ["land", "sea"],
            "state": ["Brandenburg", "Ostsee"],
            "lat": [53.0, 54.8],
            "lon": [12.0, 14.0],
            "capacity_mw": [5.0, 5.0],
            "n_units": [2, 1],
            "note": [
                "Tierra, Brandenburg, Landkreis dominante: Prignitz",
                "Mar (Báltico); parques: Baltic 1",
            ],
        }
    )
    _write(clusters, silver / "mastr" / "clusters")
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    cap = pl.DataFrame(
        {
            "date": [d for d in days for _ in range(2)],
            "cluster_id": ["bb_01", "sea_a"] * 10,
            "capacity_mw": [4.0, 5.0] * 10,
            "n_units": [1, 1] * 10,
        }
    )
    _write(cap, silver / "mastr" / "capacity_cluster")
    total = pl.DataFrame(
        {
            "date": [d for d in days for _ in range(2)],
            "kind": ["land", "sea"] * 10,
            "capacity_mw": [4.0, 5.0] * 10,
            "n_units": [1, 1] * 10,
        }
    )
    _write(total, silver / "mastr" / "capacity_total")
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    steps = 96 * 3 + 10  # tres días completos y uno incompleto
    gen = pl.DataFrame(
        {
            "ts_utc": [start + dt.timedelta(minutes=15 * i) for i in range(steps)],
            "wind_onshore_mw": [2.0] * steps,
            "wind_offshore_mw": [1.0] * steps,
            "is_gap_filled": [False] * steps,
        }
    )
    _write(gen, silver / "entsoe" / "generation")
    init = dt.datetime(2026, 1, 2)
    part = silver / "ifs_ens_clusters" / "init_date=2026-01-02"
    part.mkdir(parents=True)
    rows = [
        (init, h, init + dt.timedelta(hours=h), gid) for gid in ("bb_01", "sea_a") for h in (18, 21)
    ]
    ens = pl.DataFrame(
        {
            "init_time": [r[0] for r in rows],
            "valid_time": [r[2] for r in rows],
            "point_id": [r[3] for r in rows],
            **{
                f"wind_speed_100m_{s}": [5.0 + i for i in range(len(rows))]
                for s in ("p10", "p50", "p90", "mean")
            },
        }
    )
    ens.write_parquet(part / "part-0.parquet")


def test_daily_production_keeps_complete_days_only(tmp_path: Path) -> None:
    _synthetic_root(tmp_path)
    d = production.daily_production(Paths(root=tmp_path))
    assert d is not None
    assert d["n_days"] == 3
    assert d["onshore"] == [2, 2, 2]
    assert d["cap_onshore"] == [4, 4, 4]


def test_quality_calendar_codes(tmp_path: Path) -> None:
    _synthetic_root(tmp_path)
    q = quality.aggregate(Paths(root=tmp_path), dt.date(2026, 1, 20))
    rows = {r["key"]: r["codes"] for r in q["rows"]}
    assert len(rows["generation"]) == q["n_days"]
    i = (dt.date(2026, 1, 1) - dt.date(2015, 1, 1)).days
    assert rows["generation"][i : i + 3] == "111"
    assert rows["generation"][i + 3] == "2"  # día incompleto
    assert rows["nwp"][(dt.date(2026, 1, 2) - dt.date(2015, 1, 1)).days] == "1"
    assert rows["mastr"][i] == "1"


def test_full_build_embeds_data_and_loads_nothing_external(tmp_path: Path) -> None:
    _synthetic_root(tmp_path)
    out = tmp_path / "report" / "index.html"
    paths = Paths(root=tmp_path, nwp_bronze=tmp_path / "no-nas", mastr_raw=tmp_path / "no-nas")
    build(paths, out, now=dt.datetime(2026, 10, 16, 12, tzinfo=dt.UTC), offline=True)
    html = out.read_text("utf-8")
    assert "window.REPORT_DATA" in html
    data = build_data(paths, dt.datetime(2026, 10, 16, 12, tzinfo=dt.UTC), fetch=_fake_gh)
    assert data["zone"]["n_turbines"] == 3
    assert data["plan"]["days_to_league"] == 46
    assert data["volumes"]["nwp"]["bronze"] is None  # NAS ausente: no falla
    assert f'"n_turbines":{data["zone"]["n_turbines"]}' in html
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", html)
    assert not re.search(r"url\(\s*[\"']?https?:", html)
    assert "@import" not in html


def test_build_without_any_data_still_renders(tmp_path: Path) -> None:
    shutil.copytree(
        REPO / "report", tmp_path / "report", ignore=shutil.ignore_patterns("index.html", "*.json")
    )
    paths = Paths(root=tmp_path, nwp_bronze=tmp_path / "x", mastr_raw=tmp_path / "x")
    out = build(
        paths,
        tmp_path / "out" / "index.html",
        now=dt.datetime(2026, 10, 16, tzinfo=dt.UTC),
        offline=True,
    )
    html = out.read_text("utf-8")
    assert '"zone":null' in html
    assert '"plan":null' in html


@pytest.mark.parametrize(
    "name", ["changelog", "insights", "glossary", "sources", "labels", "model"]
)
def test_content_files_exist(name: str) -> None:
    assert (REPO / "report" / f"{name}.yaml").is_file()


def _synthetic_bronze(path: Path, init: dt.date, nlat: int = 6, nlon: int = 7) -> None:
    """Bronce mínimo (u, v a 100 m) con una sola pasada, en un Icechunk temporal."""

    repo = icechunk.Repository.create(icechunk.local_filesystem_storage(str(path)))
    session = repo.writable_session("main")
    root = zarr.open_group(session.store, mode="a")
    lats = np.linspace(52.0, 53.0, nlat)
    lons = np.linspace(12.0, 13.5, nlon)
    for name, vals in (("latitude", lats), ("longitude", lons)):
        a = root.create_array(name, shape=vals.shape, dtype=vals.dtype)
        a[:] = vals
    shape = (n_init(), len(LEAD_HOURS), N_MEMBERS, nlat, nlon)
    chunks = (1, len(LEAD_HOURS), N_MEMBERS, nlat, nlon)
    rng = np.random.default_rng(0)
    i = init_pos(init)
    for v, base_val in (("wind_u_100m", 3.0), ("wind_v_100m", 4.0)):
        a = root.create_array(v, shape=shape, chunks=chunks, dtype="float32", fill_value=np.nan)
        a[i] = (base_val + rng.normal(0, 0.1, shape[1:])).astype("float32")
    ing = root.create_array("ingested", shape=(n_init(),), dtype="int8", fill_value=0)
    ing[i] = 1
    session.commit("prueba")


def test_forecast_bronze_fields_from_synthetic_store(tmp_path: Path) -> None:

    _synthetic_bronze(tmp_path / "bronze", dt.date(2026, 10, 15))
    paths = Paths(root=tmp_path, nwp_bronze=tmp_path / "bronze")
    groups = [
        {"id": "bb_01", "kind": "land", "label": "Prignitz", "lat": 52.5, "lon": 12.7, "mw": 5.0}
    ]
    f = forecast.bronze_fields(paths, groups)
    assert f is not None
    assert f["init"] == "2026-10-15"
    assert (len(f["lats"]), len(f["lons"]), len(f["steps"])) == (6, 7, 13)
    assert len(f["steps"][0]) == 42  # una malla de 6 x 7 celdas, aplanada
    assert abs(f["steps"][0][0] - 5.0) < 0.2  # hipot(3, 4)
    m = f["members"]
    assert len(m["series"]) == 51
    assert len(m["series"][0]) == 13
    assert m["stats"]["p10"][0] <= m["stats"]["p50"][0] <= m["stats"]["p90"][0]


def test_forecast_bronze_missing_returns_none(tmp_path: Path) -> None:

    assert forecast.bronze_fields(Paths(root=tmp_path, nwp_bronze=tmp_path / "no"), []) is None


def test_forecast_config_and_variable_cards(tmp_path: Path) -> None:
    fc = forecast.config_facts(Paths(root=tmp_path))
    assert fc["variables"] == list(VARIABLES)
    assert fc["leads"][0] == 18
    assert fc["n_members"] == 51
    cards = _variables({"variables": [{"id": "wind_gust_10m", "name": "Racha", "why": "x"}]}, fc)
    assert next(c["id"] for c in cards["cards"]) == "wind_gust_10m"
    assert len(cards["cards"]) == len(VARIABLES) + len(
        fc["derived"]
    )  # los no descritos también salen


def test_forecast_vs_production_uses_target_day(tmp_path: Path) -> None:

    _synthetic_root(tmp_path)
    groups = [{"id": "bb_01", "kind": "land", "mw": 5.0}]
    # la pasada del 2 ene (sintética) cubre el 2 y el 3: el día objetivo es el 3, con producción
    part = (
        tmp_path
        / "data"
        / "silver"
        / "ifs_ens_clusters"
        / "init_date=2026-01-02"
        / "part-0.parquet"
    )
    df = pl.read_parquet(part)
    extra = df.filter(pl.col("point_id") == "bb_01").with_columns(
        pl.lit(dt.datetime(2026, 1, 3, 6)).alias("valid_time")
    )
    pl.concat([df, extra]).write_parquet(part)
    v = forecast.forecast_vs_production(Paths(root=tmp_path), groups)
    assert v is not None
    assert v["days"] == ["2026-01-03"]
    assert v["prod"] == [2]


def _scorecard_json(root: Path) -> None:
    import json  # noqa: PLC0415

    val = {
        "run_id": "r1",
        "created_at": "2026-10-16T10:00:00+00:00",
        "rmse": 1197.0,
        "mae": 800.0,
        "bias": 5.0,
        "winkler": 3709.0,
        "coverage": 0.769,
        "mean_width": 2195.0,
        "rmse_operator": 931.0,
        "vs_operator_pct": -28.6,
        "verdict": "empeora",
        "vs_best_previous_pct": None,
        "vs_best_previous_verdict": "primera ejecución",
        "days_better_than_operator_pct": 24.0,
        "n_days": 243,
        "extra": "no debe pasar",
    }
    card = {
        "generated_at": "2026-10-16T10:00:00+00:00",
        "headline": {"verdict": "empeora", "text": "Todavía no.", "g1": "no cumple"},
        "rules": {"verdict_threshold_pct": 1.0},
        "period": {"validation_start": "2025-12-01", "validation_end": "2026-07-31", "n_folds": 8},
        "holdout_evaluations_total": 0,
        "models": [
            {
                "model": "power_curve",
                "label": "Curva de potencia",
                "kind": "referencia",
                "validation": val,
                "holdout": None,
                "g1": {"status": "no cumple", "reasons": ["x"]},
                "g2": {"status": "sin evaluar"},
                "trend": {"change_pct": None, "direction": "primera ejecución"},
                "monthly": [{"month": "2025-12", "rmse": 900.0}],
                "n_runs": 1,
            }
        ],
        "history": [
            {
                "run_id": "r1",
                "model": "power_curve",
                "label": "Curva",
                "mode": "validation",
                "created_at": "2026-10-16T10:00:00+00:00",
                "rmse": 1197.0,
                "rmse_operator": 931.0,
            },
            {
                "run_id": "r2",
                "model": "power_curve",
                "label": "Curva",
                "mode": "holdout",
                "created_at": "2026-10-16T11:00:00+00:00",
                "rmse": 1.0,
                "rmse_operator": 1.0,
            },
        ],
    }
    d = root / "data" / "backtest" / "report"
    d.mkdir(parents=True)
    (d / "scorecard.json").write_text(json.dumps(card), "utf-8")


def test_backtest_chapter_empty_state_and_template(tmp_path: Path) -> None:
    from predico50hz.report import backtest  # noqa: PLC0415

    paths = Paths(root=tmp_path, nwp_bronze=tmp_path / "x", mastr_raw=tmp_path / "x")
    assert backtest.aggregate(paths) is None  # sin scorecard: estado vacío
    shutil.copytree(
        REPO / "report", tmp_path / "report", ignore=shutil.ignore_patterns("index.html", "*.json")
    )
    out = build(
        paths,
        tmp_path / "out" / "index.html",
        now=dt.datetime(2026, 10, 16, tzinfo=dt.UTC),
        offline=True,
    )
    html = out.read_text("utf-8")
    assert '"backtest":null' in html
    assert 'id="c10"' in html
    assert "¿Mejora el pronóstico?" in html


def test_backtest_chapter_reads_scorecard(tmp_path: Path) -> None:
    from predico50hz.report import backtest  # noqa: PLC0415

    _scorecard_json(tmp_path)
    paths = Paths(root=tmp_path, nwp_bronze=tmp_path / "x", mastr_raw=tmp_path / "x")
    bt = backtest.aggregate(paths)
    assert bt is not None
    assert bt["headline"]["verdict"] == "empeora"
    assert bt["models"][0]["validation"]["rmse"] == 1197.0
    assert "extra" not in bt["models"][0]["validation"]  # solo las claves que pinta el informe
    assert [h["run_id"] for h in bt["history"]] == ["r1"]  # solo validación
    data = build_data(paths, dt.datetime(2026, 10, 16, tzinfo=dt.UTC))
    assert data["backtest"]["models"][0]["label"] == "Curva de potencia"
    # un JSON corrupto o sin modelos también da estado vacío
    (tmp_path / "data" / "backtest" / "report" / "scorecard.json").write_text("{", "utf-8")
    assert backtest.aggregate(paths) is None
