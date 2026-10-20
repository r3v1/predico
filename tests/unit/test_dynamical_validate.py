# ruff: noqa: PLR2004
import datetime as dt
import json
from pathlib import Path

import icechunk
import numpy as np
import polars as pl
import pytest
import zarr

from predico50hz.ingest.dynamical.bronze import DIMS, init_pos, n_init
from predico50hz.ingest.dynamical.config import LEAD_HOURS, N_MEMBERS, VARIABLES
from predico50hz.ingest.dynamical.silver import STATS
from predico50hz.ingest.dynamical.validate import (
    GRID_SHAPE,
    N_LEADS,
    RANGES,
    SILVER_VARIABLES,
    DayResult,
    bronze_pass_stats,
    build_report,
    check_bronze_structure,
    main,
    run,
    validate_bronze_stats,
    validate_silver,
)

AFTER = dt.date(2025, 9, 1)  # con racha
BEFORE = dt.date(2024, 6, 1)  # sin racha (antes del 2024-11-13)
N_POINTS = 8
POINTS = [(f"p{k}", 50.0 + k, 10.0 + k) for k in range(N_POINTS)]


def _silver(init: dt.date = AFTER, points: list[tuple[str, float, float]] = POINTS) -> pl.DataFrame:
    """Pasada de plata sintética válida: 8 puntos x 13 pasos, estadísticos ordenados."""
    t0 = dt.datetime.combine(init, dt.time())
    rows = [(i, lat, lon, h) for i, lat, lon in points for h in LEAD_HOURS]
    n = len(rows)
    data: dict[str, object] = {
        "init_time": pl.Series([t0] * n, dtype=pl.Datetime("us")),
        "lead_hours": pl.Series([r[3] for r in rows], dtype=pl.Int32),
        "valid_time": pl.Series(
            [t0 + dt.timedelta(hours=r[3]) for r in rows], dtype=pl.Datetime("us")
        ),
        "point_id": [r[0] for r in rows],
        "lat": [r[1] for r in rows],
        "lon": [r[2] for r in rows],
    }
    for name in SILVER_VARIABLES:
        lo, hi = RANGES[name]
        mid = (lo + hi) / 2 if lo < 0 else lo + 0.3 * (hi - lo)
        vals = {
            "ctrl": mid,
            "mean": mid,
            "std": 1.0,
            "p10": mid - 0.5,
            "p50": mid,
            "p90": mid + 0.5,
        }
        for s in STATS:
            data[f"{name}_{s}"] = pl.Series(np.full(n, vals[s], dtype=np.float32))
    return pl.DataFrame(data)


def _centroids(path: Path, n: int = N_POINTS) -> Path:
    pts = "\n".join(f'  - {{"id": "p{k}", "lat": {50.0 + k}, "lon": {10.0 + k}}}' for k in range(n))
    path.write_text(f"points:\n{pts}\n")
    return path


def _checks(issues: list) -> set[tuple[str, str | None]]:
    return {(i.check, i.column) for i in issues}


# -- plata ---------------------------------------------------------------------------
def test_valid_silver_passes() -> None:
    assert validate_silver(_silver(), AFTER, N_POINTS) == []
    assert validate_silver(_silver(BEFORE), BEFORE, N_POINTS) == []


def test_silver_nan_is_flagged() -> None:
    df = _silver().with_columns(
        pl.when(pl.int_range(pl.len()) == 3)
        .then(float("nan"))
        .otherwise(pl.col("temperature_2m_mean"))
        .cast(pl.Float32)
        .alias("temperature_2m_mean")
    )
    issues = validate_silver(df, AFTER, N_POINTS)
    assert {i.cause for i in issues} == {"nan"}
    assert ("finite", "temperature_2m_mean") in _checks(issues)


def test_silver_out_of_range_is_flagged() -> None:
    df = _silver().with_columns(pl.lit(150.0, dtype=pl.Float32).alias("wind_speed_100m_p90"))
    issues = validate_silver(df, AFTER, N_POINTS)
    assert ("range", "wind_speed_100m_p90") in _checks(issues)
    assert {i.cause for i in issues} >= {"fuera_de_rango"}


def test_silver_kelvin_instead_of_celsius_is_out_of_range() -> None:
    df = _silver().with_columns((pl.col("temperature_2m_p50") + 273.15).alias("temperature_2m_p50"))
    assert ("range", "temperature_2m_p50") in _checks(validate_silver(df, AFTER, N_POINTS))


def test_silver_p10_above_p90_fails_order() -> None:
    df = _silver().with_columns(
        pl.col("wind_u_10m_p90").alias("wind_u_10m_p10"),
        pl.lit(-5.0, dtype=pl.Float32).alias("wind_u_10m_p90"),
    )
    issues = validate_silver(df, AFTER, N_POINTS)
    assert any(i.check == "stat_order" and i.cause == "falla_esquema" for i in issues)


def test_silver_negative_std_fails() -> None:
    df = _silver().with_columns(pl.lit(-0.1, dtype=pl.Float32).alias("pressure_surface_std"))
    assert ("std_range", "pressure_surface_std") in _checks(validate_silver(df, AFTER, N_POINTS))


def test_silver_missing_rows_fail() -> None:
    issues = validate_silver(_silver().head(100), AFTER, N_POINTS)
    assert {i.check for i in issues} >= {"n_rows"}
    assert {i.cause for i in issues} == {"falla_esquema"}


def test_silver_duplicate_key_fails() -> None:
    df = _silver()
    df = pl.concat([df.head(1), df.slice(1)]).with_columns(
        pl.when(pl.int_range(pl.len()) == 1)
        .then(pl.col("lead_hours").first())
        .otherwise(pl.col("lead_hours"))
        .alias("lead_hours")
    )
    assert "unique_key" in {i.check for i in validate_silver(df, AFTER, N_POINTS)}


def test_silver_wrong_lead_and_init_fail() -> None:
    df = _silver().with_columns(pl.lit(57, dtype=pl.Int32).alias("lead_hours"))
    assert "lead_grid" in {i.check for i in validate_silver(df, AFTER, N_POINTS)}
    assert "init_time" in {
        i.check for i in validate_silver(_silver(), AFTER + dt.timedelta(days=1), N_POINTS)
    }


def test_silver_missing_column_and_wrong_dtype_fail() -> None:
    assert validate_silver(_silver().drop("wind_u_100m_ctrl"), AFTER, N_POINTS)
    df = _silver().with_columns(pl.col("wind_u_100m_ctrl").cast(pl.Float64))
    assert "dtype" in {i.check for i in validate_silver(df, AFTER, N_POINTS)}


def _nan_gust(init: dt.date) -> pl.DataFrame:
    df = _silver(init)
    gust = [c for c in df.columns if c.startswith("wind_gust_10m_")]
    return df.with_columns(pl.lit(float("nan"), dtype=pl.Float32).alias(c) for c in gust)


def test_gust_nan_streak_depends_on_date() -> None:
    last_nan, first_ok = dt.date(2024, 11, 12), dt.date(2024, 11, 13)
    assert validate_silver(_nan_gust(BEFORE), BEFORE, N_POINTS) == []  # racha esperada
    assert validate_silver(_nan_gust(last_nan), last_nan, N_POINTS) == []
    assert {i.cause for i in validate_silver(_nan_gust(first_ok), first_ok, N_POINTS)} == {"nan"}
    assert {i.cause for i in validate_silver(_nan_gust(AFTER), AFTER, N_POINTS)} == {"nan"}


def test_nan_streak_only_covers_gust() -> None:
    df = _nan_gust(BEFORE).with_columns(
        pl.lit(float("nan"), dtype=pl.Float32).alias("temperature_2m_ctrl")
    )
    issues = validate_silver(df, BEFORE, N_POINTS)
    assert _checks(issues) >= {("finite", "temperature_2m_ctrl")}
    assert not any(i.column and i.column.startswith("wind_gust") for i in issues)


# -- bronce --------------------------------------------------------------------------
def _arrays(fill: float = 5.0) -> dict[str, np.ndarray]:
    shape = (N_LEADS, N_MEMBERS, *GRID_SHAPE)
    out = {}
    for v in VARIABLES:
        lo, hi = RANGES[v]
        a = np.full(shape, lo + 0.3 * (hi - lo) if lo >= 0 else fill, dtype=np.float32)
        out[v] = a
    return out


def test_valid_bronze_passes() -> None:
    stats = bronze_pass_stats(_arrays(), AFTER)
    assert stats.height == len(VARIABLES) * N_LEADS
    assert validate_bronze_stats(stats) == []


def test_bronze_nan_in_last_step_is_flagged() -> None:
    arrays = _arrays()
    arrays["temperature_2m"][-1, :, 3, 4] = np.nan  # solo el último paso, un punto
    issues = validate_bronze_stats(bronze_pass_stats(arrays, AFTER))
    assert {i.cause for i in issues} == {"nan"}
    assert issues[0].column == "temperature_2m"
    stats = bronze_pass_stats(arrays, AFTER).filter(pl.col("variable") == "temperature_2m")
    assert stats["nan_frac"].to_list()[-1] > 0
    assert stats["nan_frac"].to_list()[0] == 0


def test_bronze_dead_member_is_flagged() -> None:
    arrays = _arrays()
    arrays["pressure_surface"][4, 17] = np.nan
    issues = validate_bronze_stats(bronze_pass_stats(arrays, AFTER))
    assert "dead_members" in {i.check for i in issues}


def test_bronze_out_of_range_and_missing_members() -> None:
    arrays = _arrays()
    arrays["wind_u_100m"][2, 5, 1, 1] = 400.0
    issues = validate_bronze_stats(bronze_pass_stats(arrays, AFTER))
    assert {i.cause for i in issues} == {"fuera_de_rango"}
    arrays = _arrays()
    arrays["wind_v_10m"] = arrays["wind_v_10m"][:, :50]
    assert "members" in {i.check for i in validate_bronze_stats(bronze_pass_stats(arrays, AFTER))}


def test_bronze_gust_streak_by_date() -> None:
    arrays = _arrays()
    arrays["wind_gust_10m"][:] = np.nan
    assert validate_bronze_stats(bronze_pass_stats(arrays, BEFORE)) == []
    assert {i.cause for i in validate_bronze_stats(bronze_pass_stats(arrays, AFTER))} == {"nan"}


def test_bronze_wrong_steps_fail() -> None:
    arrays = _arrays()
    arrays["wind_v_100m"] = arrays["wind_v_100m"][:-1]  # 12 pasos
    issues = validate_bronze_stats(bronze_pass_stats(arrays, AFTER))
    assert "n_rows" in {i.check for i in issues}


# -- extremo a extremo (Icechunk sintético en disco, solo lectura) ----------------------
def _make_bronze(path: Path, days: list[dt.date], bad: dt.date | None = None) -> None:
    repo = icechunk.Repository.create(icechunk.local_filesystem_storage(str(path)))
    session = repo.writable_session("main")
    root = zarr.open_group(session.store, mode="a")
    nlat, nlon = GRID_SHAPE
    root.create_array("latitude", data=np.linspace(49.5, 56.0, nlat))
    root.create_array("longitude", data=np.linspace(8.5, 16.0, nlon))
    root.create_array("lead_time", data=np.asarray(LEAD_HOURS, dtype=np.int64))
    root.create_array("ensemble_member", data=np.arange(N_MEMBERS, dtype=np.int16))
    for v in VARIABLES:
        root.create_array(
            v,
            shape=(n_init(), N_LEADS, N_MEMBERS, nlat, nlon),
            chunks=(1, N_LEADS, N_MEMBERS, nlat, nlon),
            dtype="float32",
            fill_value=np.nan,
            dimension_names=DIMS,
        )
    root.create_array("ingested", shape=(n_init(),), dtype="int8", fill_value=0)
    root.attrs.update({"source_dataset_version": "0.1.0", "provenance": {}})
    prov = {}
    for d in days:
        for v, a in _arrays().items():
            root[v][init_pos(d)] = a
        if d == bad:
            root["temperature_2m"][init_pos(d), -1] = np.nan
        root["ingested"][init_pos(d)] = 1
        prov[d.isoformat()] = {"source_snapshot_id": "X"}
    root.attrs["provenance"] = prov
    session.commit("test")


def test_run_end_to_end_reports_gaps(tmp_path: Path) -> None:
    days = [AFTER + dt.timedelta(days=k) for k in range(5)]
    # 0: ok; 1: sin ingerir; 2: sin plata; 3: NaN en bronce (último paso); 4: plata fuera de rango
    _make_bronze(tmp_path / "bz", [days[0], days[2], days[3], days[4]], bad=days[3])
    silver = tmp_path / "silver"
    for k in (0, 3, 4):
        df = _silver(days[k])
        if k == 4:
            df = df.with_columns(pl.lit(500.0, dtype=pl.Float32).alias("wind_gust_10m_p90"))
        out = silver / f"init_date={days[k]}" / "part-0.parquet"
        out.parent.mkdir(parents=True)
        df.write_parquet(out)
    rep = run(
        days[0],
        days[-1],
        tmp_path / "bz",
        silver,
        workers=2,
        centroids=_centroids(tmp_path / "c.yaml"),
    )
    assert rep["bronze_structure_problems"] == []
    assert rep["n_covered"] == 1
    assert rep["coverage"] == pytest.approx(0.2)
    assert not rep["meets_threshold"]
    causes = {g["init_date"]: g["causes"] for g in rep["gaps"]}
    assert causes[days[1].isoformat()] == ["no_ingerida", "plata_ausente"]
    assert causes[days[2].isoformat()] == ["plata_ausente"]
    assert causes[days[3].isoformat()] == ["nan"]
    assert "fuera_de_rango" in causes[days[4].isoformat()]
    assert rep["bronze_ingested_days"] == 4


def test_cli_writes_json_and_exit_code(tmp_path: Path) -> None:
    _make_bronze(tmp_path / "bz", [AFTER])
    silver = tmp_path / "silver" / f"init_date={AFTER}"
    silver.mkdir(parents=True)
    _silver(AFTER).write_parquet(silver / "part-0.parquet")
    out = tmp_path / "rep.json"
    args = [
        "--start",
        "2025-09-01",
        "--end",
        "2025-09-01",
        "--bronze",
        str(tmp_path / "bz"),
        "--silver",
        str(tmp_path / "silver"),
        "--centroids",
        str(_centroids(tmp_path / "c.yaml")),
        "--out",
        str(out),
    ]
    assert main(args) == 0
    assert json.loads(out.read_text())["coverage"] == 1.0
    assert main([*args[:3], "2025-09-02", *args[4:]]) == 1  # una pasada sin ingerir


def test_bronze_structure_problems(tmp_path: Path) -> None:
    _make_bronze(tmp_path / "bz", [AFTER])
    repo = icechunk.Repository.open(icechunk.local_filesystem_storage(str(tmp_path / "bz")))
    root = zarr.open_group(repo.readonly_session(branch="main").store, mode="r")
    assert check_bronze_structure(root) == []


def test_coverage_threshold_in_report() -> None:
    ok = [DayResult(f"2025-09-{d:02d}", True, True) for d in range(1, 29)]
    bad = DayResult("2025-09-29", False, False)
    rep = build_report([*ok, bad], dt.date(2025, 9, 1), dt.date(2025, 9, 29), {})
    assert rep["n_covered"] == 28
    assert rep["coverage"] == pytest.approx(28 / 29)
    assert rep["meets_threshold"] is False  # 96,6 % < 98 %
    rep = build_report(ok, dt.date(2025, 9, 1), dt.date(2025, 9, 28), {})
    assert rep["meets_threshold"] is True


def test_validate_silver_variable_number_of_points(tmp_path: Path) -> None:
    pts21 = [(f"q{k}", 50.0 + k * 0.1, 10.0 + k * 0.1) for k in range(21)]
    df = _silver(points=pts21)
    assert df.height == 21 * N_LEADS
    assert validate_silver(df, AFTER, 21) == []
    assert "n_rows" in {i.check for i in validate_silver(df, AFTER, N_POINTS)}  # 8 != 21
    assert "n_points" in {i.check for i in validate_silver(_silver(), AFTER, 21)}


def test_run_uses_number_of_points_from_centroids(tmp_path: Path) -> None:
    _make_bronze(tmp_path / "bz", [AFTER])
    silver = tmp_path / "silver" / f"init_date={AFTER}"
    silver.mkdir(parents=True)
    _silver(AFTER).write_parquet(silver / "part-0.parquet")
    run_ = lambda n: run(  # noqa: E731
        AFTER,
        AFTER,
        tmp_path / "bz",
        tmp_path / "silver",
        1,
        centroids=_centroids(tmp_path / "c.yaml", n),
    )
    assert run_(8)["coverage"] == 1.0
    rep = run_(21)
    assert rep["coverage"] == 0.0
    assert rep["n_points"] == 21
