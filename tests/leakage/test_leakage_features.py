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
from predico50hz.features import price as price_mod
from predico50hz.features.build import build_gold
from predico50hz.features.capacity import load_clusters
from predico50hz.features.config import DEFAULT_GOLD
from predico50hz.features.registry import (
    BLOCKS,
    PRICE_FEATURES,
    TARGETS,
    Column,
    available_col,
    build_registry,
    feature_columns,
)
from predico50hz.features.timing import closure_utc, price_bound, target_window
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
    late_price = df.with_columns(available_at_price=pl.col("closure_utc"))
    assert any("available_at_price" in p for p in check_no_lookahead(late_price))
    assert any(
        i["check"] == "available_before_closure" for i in schema_issues(registry, late_price)
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
    forbidden = re.compile(r"^y_|^wind_.*_mw$|forecast|operator|operador|split_v01|^target_")
    assert [f for f in feats if forbidden.search(f)] == []
    # El precio solo entra como el bloque `price` (precio de D), siempre con prefijo `price_`.
    assert [f for f in feats if "price" in f and f not in PRICE_FEATURES] == []
    assert all(f.startswith("price_") for f in PRICE_FEATURES)
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
    banned_text = re.compile(r"entsoe[/.]forecast|ingest\.entsoe|silver/entsoe/forecast")
    for f in sorted(pkg.glob("*.py")):
        src = f.read_text()
        assert not banned_text.search(src), f"{f.name} referencia el pronóstico de ENTSO-E"
        # La plata de precios solo se nombra en la configuración (ruta por defecto).
        if f.name != "config.py":
            assert "silver/entsoe/prices" not in src, f.name
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"forecast", "prices"}, f"{f.name}: dataset {node.value!r}"
                assert not re.search(r"(^|/)forecast(/|$)", node.value), f.name
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = [getattr(node, "module", None) or "", *(a.name for a in node.names)]
                assert not any("entsoe" in n for n in names), f"{f.name} importa ENTSO-E"


def test_price_of_next_day_never_enters(synth: Synth, registry: list[Column]) -> None:
    """Alterar o quitar los precios desde el día local D+1 no cambia ninguna variable de D."""
    feats = ["ts_utc", *feature_columns(registry), "available_at_price"]
    for d in DATES:
        first_next = target_window(d)[0]  # primer instante del día local D+1
        synth.write_prices()
        base, _ = build_gold(synth.sources, d, d)
        for shift in (500.0, -777.0):
            synth.write_prices(shift_after=first_next, shift=shift)
            changed, _ = build_gold(synth.sources, d, d)
            assert_frame_equal(base.select(feats), changed.select(feats), check_exact=True)
        # Sin ningún precio de D+1 en adelante tampoco cambia.
        px = pl.read_delta(synth.sources.prices).filter(pl.col("ts_utc") < first_next)
        px.write_delta(synth.sources.prices, mode="overwrite")
        cut, _ = build_gold(synth.sources, d, d)
        assert_frame_equal(base.select(feats), cut.select(feats), check_exact=True)


def test_price_of_d_does_change_the_price_block(synth: Synth, registry: list[Column]) -> None:
    d = DATES[1]
    first_d = target_window(d - dt.timedelta(days=1))[0]  # primer instante del día local D
    base, _ = build_gold(synth.sources, d, d)
    synth.write_prices(shift_after=first_d, shift=40.0)
    changed, _ = build_gold(synth.sources, d, d)
    assert changed["price_mean"][0] == pytest.approx(base["price_mean"][0] + 40.0)
    assert changed["price_min"][0] == pytest.approx(base["price_min"][0] + 40.0)
    assert changed["price_range"][0] == pytest.approx(base["price_range"][0])
    others = [c for c in feature_columns(registry) if not c.startswith("price_")]
    assert_frame_equal(base.select(others), changed.select(others), check_exact=True)


def test_available_at_price_is_before_closure_with_margin(synth: Synth) -> None:
    df, _ = build_gold(synth.sources)
    assert (df["available_at_price"] < df["closure_utc"]).all()
    assert (df["closure_utc"] - df["available_at_price"]).min() >= dt.timedelta(hours=17)
    for d in DATES:  # la cota prudente también con los cambios de hora (96, 92 y 100 pasos)
        assert closure_utc(d) - price_bound(d) >= dt.timedelta(hours=17)


def test_price_arriving_after_closure_is_not_used(synth: Synth) -> None:
    """Un precio medido que llegó tras el cierre de D deja el día con NaN y `price_missing`."""
    d = DATES[3]
    px = pl.read_delta(synth.sources.prices)
    late = closure_utc(d) + dt.timedelta(minutes=1)
    lo, hi = price_mod.delivery_window(d)
    px = px.with_columns(
        first_seen_at=pl.when(pl.col("ts_utc").is_between(lo, hi, closed="left"))
        .then(pl.lit(late).cast(pl.Datetime("us", "UTC")))
        .otherwise(pl.col("first_seen_at"))
    )
    px.write_delta(synth.sources.prices, mode="overwrite")
    df, _ = build_gold(synth.sources, d, d)
    assert df["price_missing"].unique().to_list() == [1]
    assert df.select(pl.col("price_mean").is_nan().all()).item()
    assert (df["available_at_price"] < df["closure_utc"]).all()
