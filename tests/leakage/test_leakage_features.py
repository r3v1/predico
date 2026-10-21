"""Anti look-ahead de la capa de variables (F3): ninguna feature usa datos posteriores al cierre.

Todas las pruebas usan datos sintéticos (`tests/conftest.py`) y no dependen de `data/`, salvo la
primera, que además revisa la tabla oro real si existe.
"""

import ast
import datetime as dt
import re
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import predico50hz.features as features_pkg
from conftest import DATES, Synth
from predico50hz.features.build import build_gold
from predico50hz.features.capacity import load_clusters
from predico50hz.features.config import DEFAULT_GOLD
from predico50hz.features.registry import (
    BLOCKS,
    TARGETS,
    Column,
    available_col,
    build_registry,
    feature_columns,
)
from predico50hz.features.timing import closure_utc
from predico50hz.features.validate import schema_issues

REPO = Path(__file__).resolve().parents[2]
AVAILABLE = [available_col(b) for b in BLOCKS]


def check_no_lookahead(df: pl.DataFrame) -> list[str]:
    """Problemas de look-ahead: available_at ≥ cierre, cierre mal calculado o init ≠ D 00:00."""
    problems: list[str] = []
    for a in AVAILABLE:
        n = df.filter(pl.col(a) >= pl.col("closure_utc")).height
        if n:
            problems.append(f"{a} >= closure_utc en {n} filas")
    bad_init = df.filter(
        pl.col("nwp_init_time")
        != pl.col("forecast_issue_date").cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    )
    if bad_init.height:
        problems.append(f"nwp_init_time distinto de D 00:00 UTC en {bad_init.height} filas")
    for d in df.get_column("forecast_issue_date").unique().to_list():
        got = df.filter(pl.col("forecast_issue_date") == d)["closure_utc"].unique().to_list()
        if got != [closure_utc(d)]:
            problems.append(f"closure_utc incorrecto el {d}")
    return problems


@pytest.fixture
def registry(synth: Synth) -> list[Column]:
    return build_registry(load_clusters(synth.sources.centroids))


def test_synthetic_gold_has_no_lookahead(synth: Synth) -> None:
    df, _ = build_gold(synth.sources)
    assert check_no_lookahead(df) == []
    # Con cierre de 13:00 UTC (verano) y 14:00 (invierno), la pasada llega 8 h 30 min tras 00Z.
    assert (df["closure_utc"] - df["available_at_nwp"]).min() >= dt.timedelta(hours=4)


def test_leakage_check_detects_a_violation(synth: Synth, registry: list[Column]) -> None:
    df, _ = build_gold(synth.sources)
    late = df.with_columns(available_at_nwp=pl.col("closure_utc") + pl.duration(minutes=1))
    assert check_no_lookahead(late)
    assert any(i["check"] == "available_before_closure" for i in schema_issues(registry, late))
    other_pass = df.with_columns(nwp_init_time=pl.col("nwp_init_time") + pl.duration(days=1))
    assert check_no_lookahead(other_pass)
    assert any(
        i["check"] == "nwp_init_is_issue_day_00z" for i in schema_issues(registry, other_pass)
    )


@pytest.mark.data
def test_real_gold_has_no_lookahead() -> None:
    path = REPO / DEFAULT_GOLD
    if not (path / "_delta_log").exists():
        pytest.skip("no existe la tabla oro real")
    df = pl.read_delta(str(path)).select(
        "forecast_issue_date", "closure_utc", "nwp_init_time", *AVAILABLE
    )
    assert check_no_lookahead(df) == []


def test_every_feature_has_an_availability_block(synth: Synth, registry: list[Column]) -> None:
    df, _ = build_gold(synth.sources)
    for c in registry:
        if c.role == "feature":
            assert c.block in BLOCKS, c.name
            assert available_col(c.block) in df.columns  # pyright: ignore[reportArgumentType]
        else:
            assert c.block is None
    for b in BLOCKS:
        assert any(c.block == b for c in registry), f"bloque {b} sin variables"
    assert set(df.columns) == {c.name for c in registry}


def test_forbidden_columns_are_never_features(registry: list[Column]) -> None:
    feats = feature_columns(registry)
    forbidden = re.compile(r"^y_|^wind_.*_mw$|forecast|operator|operador|price|split_v01|^target_")
    assert [f for f in feats if forbidden.search(f)] == []
    assert set(feats).isdisjoint(TARGETS)
    assert set(feats).isdisjoint(
        {"y_cap_mw", "y_cap_land_mw", "y_cap_sea_mw", "split_v01", "ts_utc"}
    )
    assert not any(f.startswith("available_at") or f.endswith("_date") for f in feats)
    assert all(c.name in TARGETS for c in registry if c.role == "target")


def test_features_invariant_to_generation_and_operator_forecast(
    synth: Synth, registry: list[Column]
) -> None:
    feats = ["forecast_issue_date", "ts_utc", *feature_columns(registry), *AVAILABLE]
    base, _ = build_gold(synth.sources)
    synth.write_generation(scale=3.7)
    synth.write_operator_forecast()
    perturbed, _ = build_gold(synth.sources)
    assert_frame_equal(base.select(feats), perturbed.select(feats), check_exact=True)
    # La generación sí cambia el objetivo (la prueba tiene dientes).
    assert base["y_gen_mw"].sum() != perturbed["y_gen_mw"].sum()
    synth.write_generation(hole_after=dt.datetime(2025, 11, 1, tzinfo=dt.UTC))
    empty, _ = build_gold(synth.sources)
    assert_frame_equal(base.select(feats), empty.select(feats), check_exact=True)


def test_next_day_pass_is_never_used(synth: Synth, registry: list[Column]) -> None:
    d = DATES[1]  # 2026-01-15
    feats = [*feature_columns(registry), "ts_utc"]
    synth.write_pass(d + dt.timedelta(days=1))
    base, _ = build_gold(synth.sources, d, d)
    synth.write_pass(d + dt.timedelta(days=1), bump=0.5)
    changed, _ = build_gold(synth.sources, d, d)
    assert_frame_equal(base.select(feats), changed.select(feats), check_exact=True)
    # Y también si se pide el rango completo: D no cambia por existir D+1.
    full, _ = build_gold(synth.sources)
    only_d = full.filter(pl.col("forecast_issue_date") == d)
    assert_frame_equal(base.select(feats), only_d.select(feats), check_exact=True)
    # Una pasada anterior distinta tampoco influye en D.
    synth.write_pass(d - dt.timedelta(days=1), bump=0.9)
    prev, _ = build_gold(synth.sources, d, d)
    assert_frame_equal(base.select(feats), prev.select(feats), check_exact=True)


def test_changing_the_pass_of_d_does_change_features(synth: Synth, registry: list[Column]) -> None:
    d = DATES[1]
    feats = feature_columns(registry)
    base, _ = build_gold(synth.sources, d, d)
    synth.write_pass(d, bump=0.5)
    changed, _ = build_gold(synth.sources, d, d)
    assert not base.select(feats).equals(changed.select(feats))


def test_no_day_without_its_own_pass(synth: Synth) -> None:
    """Sin la pasada de D no se rellena con la de D-1 ni la de D+1."""
    d = DATES[1]
    synth.write_pass(d - dt.timedelta(days=1))
    synth.write_pass(d + dt.timedelta(days=1))
    (Path(synth.sources.nwp) / f"init_date={d}" / "part-0.parquet").unlink()
    df, _ = build_gold(synth.sources)
    assert d not in set(df["forecast_issue_date"])


def test_features_package_never_references_operator_forecast_or_prices() -> None:
    pkg = Path(features_pkg.__file__).parent
    banned_text = re.compile(
        r"entsoe[/.](forecast|prices)|ingest\.entsoe|silver/entsoe/(forecast|prices)"
    )
    for f in sorted(pkg.glob("*.py")):
        src = f.read_text()
        assert not banned_text.search(src), f"{f.name} referencia forecast/prices de ENTSO-E"
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"forecast", "prices"}, f"{f.name}: dataset {node.value!r}"
                assert not re.search(r"(^|/)(forecast|prices)(/|$)", node.value), f.name
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = [getattr(node, "module", None) or "", *(a.name for a in node.names)]
                assert not any("entsoe" in n for n in names), f"{f.name} importa ENTSO-E"
