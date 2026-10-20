# ruff: noqa: PLR2004, PT011
import datetime as dt
import hashlib

import numpy as np
import pyarrow.parquet as pq
import pytest
import xarray as xr

from predico50hz.ingest.dynamical.bronze import BronzeStore, init_pos
from predico50hz.ingest.dynamical.config import LEAD_HOURS, N_MEMBERS, SPEEDS, VARIABLES, Domain
from predico50hz.ingest.dynamical.rebuild_silver import (
    META_BRONZE_DIGEST,
    META_CENTROIDS_SHA,
    META_SNAPSHOT,
    rebuild,
    silver_meta,
)
from predico50hz.ingest.dynamical.silver import (
    STATS,
    build_table,
    ensemble_stats,
    silver_path,
    table_digest,
    wind_speed,
)
from predico50hz.ingest.dynamical.source import (
    Source,
    axis_slice,
    lead_slice,
    read_cube,
)
from predico50hz.ingest.ifs.extract import Point, load_points

DOM = Domain(49.5, 56.0, 8.5, 16.0)


def _fake_source(fill: float = 1.0) -> Source:
    """Mini-origen: lat descendente 60..45, lon -10..20, 20 pasos 3-horarios, 51 miembros."""
    lat = np.arange(60.0, 44.99, -0.25)
    lon = np.arange(-10.0, 20.01, 0.25)
    lead = (np.arange(0, 60, 3) * np.timedelta64(1, "h")).astype("timedelta64[ns]")
    init = np.array(["2026-10-03", "2026-10-04"], dtype="datetime64[ns]")
    member = np.arange(N_MEMBERS)
    shape = (2, len(lead), N_MEMBERS, len(lat), len(lon))
    # el valor codifica la latitud: permite detectar si se invirtio mal el eje
    base = np.broadcast_to(lat[None, None, None, :, None], shape).astype(np.float32) * fill
    dims = ("init_time", "lead_time", "ensemble_member", "latitude", "longitude")
    ds = xr.Dataset(
        {v: (dims, base.copy()) for v in VARIABLES},
        coords={
            "init_time": init,
            "lead_time": lead,
            "ensemble_member": member,
            "latitude": lat,
            "longitude": lon,
        },
        attrs={"dataset_version": "0.1.0"},
    )
    return Source(ds, "fake://href", "SNAP", "0.1.0")


def test_ensemble_stats_known_values() -> None:
    x = np.arange(51, dtype=np.float64)[:, None] * np.ones((1, 4))  # (member, lead)
    s = ensemble_stats(x, axis=0)
    assert set(s) == set(STATS)
    np.testing.assert_allclose(s["ctrl"], 0.0)
    np.testing.assert_allclose(s["mean"], 25.0)
    np.testing.assert_allclose(s["std"], np.arange(51).std(ddof=1))
    np.testing.assert_allclose(s["p10"], 5.0)
    np.testing.assert_allclose(s["p50"], 25.0)
    np.testing.assert_allclose(s["p90"], 45.0)


def test_control_is_member_zero_not_mean() -> None:
    x = np.full((51, 2), 3.0)
    x[0] = 100.0
    s = ensemble_stats(x, axis=0)
    np.testing.assert_allclose(s["ctrl"], 100.0)
    assert float(s["mean"][0]) == pytest.approx((100 + 50 * 3) / 51)


def test_speed_per_member_differs_from_speed_of_mean() -> None:
    u = np.array([[3.0, -3.0]] * 1).T  # (member, lead): +3 y -3
    v = np.zeros_like(u)
    per_member = ensemble_stats(wind_speed(u, v), axis=0)["mean"]
    of_mean = wind_speed(u.mean(axis=0), v.mean(axis=0))
    np.testing.assert_allclose(per_member, 3.0)
    np.testing.assert_allclose(of_mean, 0.0)


def test_axis_slice_descending_and_ascending() -> None:
    desc = np.arange(60.0, 44.99, -0.25)
    sl = axis_slice(desc, 49.5, 56.0)
    assert desc[sl][0] == 56.0
    assert desc[sl][-1] == 49.5
    assert len(desc[sl]) == 27
    asc = np.arange(-10.0, 20.01, 0.25)
    sl = axis_slice(asc, 8.5, 16.0)
    assert (asc[sl][0], asc[sl][-1], len(asc[sl])) == (8.5, 16.0, 31)


def test_axis_slice_errors() -> None:
    with pytest.raises(ValueError):
        axis_slice(np.array([0.0, 1.0]), 5.0, 6.0)
    with pytest.raises(ValueError):
        axis_slice(np.array([0.0, 5.0, 1.0]), 0.0, 1.0)  # no contiguo


def test_lead_slice_selects_values_not_positions() -> None:
    lead = (np.arange(0, 60, 3) * np.timedelta64(1, "h")).astype("timedelta64[ns]")
    sl = lead_slice(lead)
    assert (lead[sl] / np.timedelta64(1, "h")).astype(int).tolist() == list(LEAD_HOURS)
    with pytest.raises(ValueError):  # falta el paso +54 h
        lead_slice(lead[:17])


def test_read_cube_crops_domain_and_flips_latitude() -> None:
    src = _fake_source()
    cube = read_cube(src, np.datetime64("2026-10-04", "ns"), VARIABLES, DOM, workers=3)
    assert cube.lats[0] == 49.5
    assert cube.lats[-1] == 56.0
    assert cube.lons[0] == 8.5
    assert cube.lons[-1] == 16.0
    u = cube.data["wind_u_100m"]
    assert u.shape == (len(LEAD_HOURS), N_MEMBERS, 27, 31)
    assert u.dtype == np.float32
    # valor == latitud de la celda: tras invertir debe coincidir con cube.lats
    np.testing.assert_allclose(u[3, 7, :, 5], cube.lats)


def test_read_cube_unknown_init_raises() -> None:
    with pytest.raises(KeyError):
        read_cube(_fake_source(), np.datetime64("2030-01-01", "ns"), VARIABLES, DOM)


def test_build_table_columns_and_speed() -> None:
    src = _fake_source()
    cube = read_cube(src, np.datetime64("2026-10-04", "ns"), VARIABLES, DOM)
    # u alterna +-3 por miembro, v = 0
    sign = np.where(np.arange(N_MEMBERS) % 2 == 0, 3.0, -3.0).astype(np.float32)
    cube.data["wind_u_100m"][:] = sign[None, :, None, None]
    cube.data["wind_v_100m"][:] = 0.0
    pts = [Point("a", 53.3, 13.9), Point("b", 51.1, 9.2)]
    t = build_table(cube, pts, cube.init, {"k": "v"})
    assert t.num_rows == len(pts) * len(LEAD_HOURS)
    for name in (*VARIABLES, *SPEEDS):
        for stat in STATS:
            assert f"{name}_{stat}" in t.column_names
    np.testing.assert_allclose(t["wind_speed_100m_mean"].to_numpy(), 3.0, rtol=1e-6)
    np.testing.assert_allclose(t["wind_u_100m_mean"].to_numpy(), 3.0 / 51, atol=1e-6)
    assert set(t["point_id"].to_pylist()) == {"a", "b"}
    assert t["lead_hours"].to_pylist()[: len(LEAD_HOURS)] == list(LEAD_HOURS)


def test_bilinear_point_value_between_cells() -> None:
    src = _fake_source()
    cube = read_cube(src, np.datetime64("2026-10-04", "ns"), VARIABLES, DOM)
    t = build_table(cube, [Point("p", 53.30, 13.90)], cube.init, {})
    # la variable vale la latitud: la bilineal debe devolver 53.30 (lineal en latitud)
    np.testing.assert_allclose(t["temperature_2m_mean"].to_numpy(), 53.30, atol=1e-4)
    np.testing.assert_allclose(t["temperature_2m_std"].to_numpy(), 0.0, atol=1e-5)


def test_bronze_write_is_idempotent_and_records_provenance(tmp_path) -> None:
    src = _fake_source()
    day = dt.date(2026, 10, 4)
    cube = read_cube(src, np.datetime64(day.isoformat(), "ns"), VARIABLES, DOM)
    store = BronzeStore(tmp_path / "bronze")
    c1 = store.write_pass(cube, day, src, "pasada 1", VARIABLES)
    d1 = store.digest(day)
    assert c1 is not None
    assert store.is_ingested(day)
    assert not store.is_ingested(dt.date(2026, 10, 5))
    store.write_pass(cube, day, src, "pasada 1 (repetida)", VARIABLES)
    assert store.digest(day) == d1
    ds = store.read_pass(day)
    assert ds["wind_u_100m"].shape == (1, len(LEAD_HOURS), N_MEMBERS, 27, 31)
    assert ds.attrs["provenance"]["2026-10-04"]["source_snapshot_id"] == "SNAP"
    assert ds.attrs["source_dataset_version"] == "0.1.0"
    assert table_digest(build_table(cube, [Point("a", 53.0, 13.0)], cube.init, {})) == table_digest(
        build_table(cube, [Point("a", 53.0, 13.0)], cube.init, {"otro": "meta"})
    )


def test_init_pos_bounds() -> None:
    assert init_pos(dt.date(2024, 4, 1)) == 0
    assert init_pos(dt.date(2024, 4, 2)) == 1
    with pytest.raises(ValueError):
        init_pos(dt.date(2024, 3, 31))


def _bronze_with_pass(tmp_path, day: dt.date):
    src = _fake_source()
    cube = read_cube(src, np.datetime64(day.isoformat(), "ns"), VARIABLES, DOM)
    store = BronzeStore(tmp_path / "bronze")
    store.write_pass(cube, day, src, "pasada", VARIABLES)
    return cube


def _centroids_file(path, points: list[tuple[str, float, float]]):
    rows = "\n".join(
        f'  - {{"id": "{i}", "lat": {la}, "lon": {lo}, "kind": "land"}}' for i, la, lo in points
    )
    path.write_text(f"points:\n{rows}\n")
    return path


def test_rebuild_silver_from_bronze_matches_direct_and_is_idempotent(tmp_path) -> None:
    day = dt.date(2026, 10, 4)
    cube = _bronze_with_pass(tmp_path, day)
    pts = [("a", 53.1, 13.2), ("b", 51.3, 10.6), ("c", 54.4, 15.1)]
    cfile = _centroids_file(tmp_path / "c.yaml", pts)
    silver = tmp_path / "silver"
    args = (day, day + dt.timedelta(days=1), tmp_path / "bronze", silver, cfile)
    res = rebuild(*args, workers=2)
    assert [r.status for r in res] == ["ok", "not_ingested"]
    out = silver_path(silver, day.isoformat())
    sha1 = out.read_bytes()
    meta = silver_meta(out)
    assert meta[META_CENTROIDS_SHA] == hashlib.sha256(cfile.read_bytes()).hexdigest()
    assert meta[META_BRONZE_DIGEST] == BronzeStore(tmp_path / "bronze").digest(day)
    assert meta[META_SNAPSHOT]
    direct = build_table(cube, load_points(cfile), cube.init, {})
    got = pq.read_table(out)
    assert table_digest(got) == table_digest(direct)
    assert got.num_rows == len(pts) * len(LEAD_HOURS)
    # segunda ejecución: mismos hashes -> no reescribe
    assert [r.status for r in rebuild(*args)] == ["unchanged", "not_ingested"]
    assert out.read_bytes() == sha1
    assert [r.status for r in rebuild(*args, force=True)] == ["ok", "not_ingested"]


def test_rebuild_silver_regenerates_when_centroids_change_and_n_points_varies(tmp_path) -> None:
    day = dt.date(2026, 10, 4)
    _bronze_with_pass(tmp_path, day)
    silver = tmp_path / "silver"
    n_rows = []
    for k, n in enumerate((2, 5)):
        pts = [(f"p{j}", 52.0 + j * 0.3, 12.0 + j * 0.2) for j in range(n)]
        cfile = _centroids_file(tmp_path / f"c{k}.yaml", pts)
        assert rebuild(day, day, tmp_path / "bronze", silver, cfile)[0].status == "ok"
        n_rows.append(pq.read_table(silver_path(silver, day.isoformat())).num_rows)
    assert n_rows == [2 * len(LEAD_HOURS), 5 * len(LEAD_HOURS)]


def test_rebuild_silver_does_not_write_bronze(tmp_path) -> None:
    day = dt.date(2026, 10, 4)
    _bronze_with_pass(tmp_path, day)
    store = BronzeStore(tmp_path / "bronze")
    before = store.repo.lookup_branch("main")
    cfile = _centroids_file(tmp_path / "c.yaml", [("a", 53.0, 13.0)])
    rebuild(day, day, tmp_path / "bronze", tmp_path / "silver", cfile)
    assert BronzeStore(tmp_path / "bronze").repo.lookup_branch("main") == before
