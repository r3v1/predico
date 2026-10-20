# ruff: noqa: PLR2004, PLR0913, PLR0917
"""Pruebas sin red de la ingesta de MaStR con datos sintéticos."""

import datetime as dt
import zipfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from predico50hz.ingest.ifs.extract import load_points
from predico50hz.ingest.mastr import compare, validate
from predico50hz.ingest.mastr.capacity import daily_capacity
from predico50hz.ingest.mastr.centroids import write_centroids
from predico50hz.ingest.mastr.clusterize import (
    GRID_NS_KM,
    KRow,
    assign_clusters,
    choose_k,
    in_domain,
    land_clusters,
    sea_clusters,
)
from predico50hz.ingest.mastr.clusters import (
    agreement,
    group_by_proximity,
    kmeans_weighted,
    to_km,
)
from predico50hz.ingest.mastr.extract import WIND_FIELDS
from predico50hz.ingest.mastr.units import resolve_coordinates, typed_units
from predico50hz.ingest.mastr.xmlstream import iter_records, members
from predico50hz.ingest.mastr.zone import assign_zone

D = dt.date


def zone_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    base: dict[str, object] = {
        "regelzone": None,
        "regelzone_unit": None,
        "bundesland": "Brandenburg",
        "is_offshore": False,
        "sea_area": None,
    }
    schema = {
        "regelzone": pl.String,
        "regelzone_unit": pl.String,
        "bundesland": pl.String,
        "is_offshore": pl.Boolean,
        "sea_area": pl.String,
    }
    return pl.DataFrame([base | r for r in rows], schema=schema)


def test_zone_rule_priority_and_doubtful() -> None:
    df = assign_zone(
        zone_frame(
            [
                {"regelzone": "50Hertz", "bundesland": "Niedersachsen"},  # Amt Neuhaus
                {"regelzone": "TenneT", "bundesland": "Thüringen"},  # Bundesland diría sí
                {"regelzone_unit": "50Hertz", "bundesland": "Bayern"},  # solo campo provisional
                {"bundesland": "Sachsen"},  # sin Regelzone: Bundesland
                {"bundesland": "Hessen"},
                {"regelzone": "50Hertz", "bundesland": "Hamburg"},
                {"bundesland": "Hamburg"},  # sin Regelzone y Hamburg: fuera
                {"is_offshore": True, "sea_area": "Ostsee", "bundesland": "AWZ"},
                {"is_offshore": True, "sea_area": "Nordsee", "bundesland": "AWZ"},
                {"regelzone": "TenneT", "is_offshore": True, "sea_area": "Ostsee"},
            ]
        )
    )
    assert df["in_zone"].to_list() == [
        True,
        False,
        True,
        True,
        False,
        True,
        False,
        True,
        False,
        False,
    ]
    assert df["zone_rule"].to_list()[:4] == [
        "netzanschlusspunkt",
        "netzanschlusspunkt",
        "einheit",
        "bundesland",
    ]
    doubtful = df["zone_doubtful"].to_list()
    assert [doubtful[i] for i in (0, 1, 5, 6, 9)] == [True] * 5
    assert [doubtful[i] for i in (3, 7)] == [False] * 2


def test_capacity_with_commissioning_and_decommissioning() -> None:
    units = pl.DataFrame(
        {
            "kind": ["land", "land", "land", "sea", "land"],
            "net_mw": [2.0, 3.0, 5.0, 10.0, 7.0],
            "start_date": [D(2014, 6, 1), D(2015, 1, 3), D(2015, 1, 5), D(2015, 1, 2), None],
            "end_date": [D(2015, 1, 4), None, D(2015, 1, 5), None, None],
        }
    )
    cap = daily_capacity(units, "kind", D(2015, 1, 1), D(2015, 1, 6))
    land = cap.filter(pl.col("kind") == "land").sort("date")
    # d1: 2 (anterior a la serie); d3: +3; d4: la baja cuenta desde ese día;
    # d5: alta y baja el mismo día
    assert land["capacity_mw"].to_list() == [2.0, 2.0, 5.0, 3.0, 3.0, 3.0]
    assert land["n_units"].to_list() == [1, 1, 2, 1, 1, 1]
    sea = cap.filter(pl.col("kind") == "sea").sort("date")
    assert sea["capacity_mw"].to_list() == [0.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    assert cap.height == 12  # 6 días por 2 grupos, sin la unidad sin fecha de alta
    schema = validate.capacity_schema("kind")
    assert not validate.schema_issues(schema, cap)


def blobs(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centers = np.array([[0.0, 0.0], [100.0, 0.0], [0.0, 100.0], [100.0, 100.0]])
    xy = np.vstack([c + rng.normal(0, 4.0, (50, 2)) for c in centers])
    return xy, rng.uniform(1.0, 3.0, len(xy))


def test_kmeans_reproducible_and_recovers_blobs() -> None:
    xy, w = blobs()
    a = kmeans_weighted(xy, w, 4, seed=7)
    b = kmeans_weighted(xy, w, 4, seed=7)
    assert np.array_equal(a.labels, b.labels)
    assert np.allclose(a.centers, b.centers)
    assert a.rmse_km < 8.0
    assert len({tuple(np.round(c, -1)) for c in a.centers}) == 4
    assert agreement(a.labels, kmeans_weighted(xy, w, 4, seed=8).labels) == pytest.approx(1.0)


def test_kmeans_weights_pull_centroid() -> None:
    xy = np.array([[0.0, 0.0], [10.0, 0.0]])
    r = kmeans_weighted(xy, np.array([1.0, 9.0]), 1, seed=0)
    assert r.centers[0, 0] == pytest.approx(9.0)
    with pytest.raises(ValueError, match="pesos"):
        kmeans_weighted(xy, np.array([1.0, 0.0]), 1)


def test_group_by_proximity_chains_and_choose_k() -> None:
    xy = np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0], [50.0, 0.0]])
    assert group_by_proximity(xy, 6.0).tolist() == [0, 0, 0, 1]
    rows = [
        KRow(15, GRID_NS_KM + 1, None, 30.0, 0.99, 10.0, 100.0),
        KRow(16, GRID_NS_KM - 1, 3.0, 30.0, 0.90, 10.0, 100.0),  # inestable
        KRow(17, GRID_NS_KM - 2, 3.0, 30.0, 0.99, 10.0, 100.0),
    ]
    assert choose_k(rows) == 17
    assert choose_k(rows[:2]) == 16  # ninguno cumple: el mayor del rango


def synthetic_units(seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = 240
    centers = np.array([[53.5, 12.0], [52.0, 13.5], [51.2, 11.0], [53.0, 14.5]])
    c = centers[rng.integers(0, 4, n)]
    return pl.DataFrame(
        {
            "unit_id": [f"U{i}" for i in range(n)],
            "kind": ["land"] * n,
            "in_zone": [True] * n,
            "status": ["In Betrieb"] * n,
            "lat": c[:, 0] + rng.normal(0, 0.1, n),
            "lon": c[:, 1] + rng.normal(0, 0.1, n),
            "net_mw": rng.uniform(1.0, 6.0, n),
            "bundesland": ["Brandenburg"] * n,
            "landkreis": ["X"] * n,
            "windpark": [None] * n,
            "start_date": [D(2010, 1, 1)] * n,
            "end_date": [None] * n,
        },
        schema_overrides={"windpark": pl.String, "end_date": pl.Date},
    )


def test_land_clusters_reproducible_and_inside_domain() -> None:
    u = synthetic_units()
    a, _, _ = land_clusters(u, k=4, seed=42)
    b, _, _ = land_clusters(u, k=4, seed=42)
    assert a.equals(b)
    assert a.height == 4
    assert in_domain(a).is_empty()
    assert a["capacity_mw"].sum() == pytest.approx(u["net_mw"].sum())
    assigned = assign_clusters(u, a)
    assert assigned["cluster_id"].null_count() == 0
    assert set(assigned["cluster_id"].unique()) == set(a["cluster_id"])


def test_sea_clusters_merge_close_parks() -> None:
    parks = [  # dos parques a ~5 km y uno a ~100 km
        ("A", 54.80, 14.00),
        ("B", 54.80, 14.07),
        ("C", 54.60, 12.65),
    ]
    rows = [(f"{p}{i}", p, la, lo) for p, la, lo in parks for i in range(3)]
    u = pl.DataFrame(
        {
            "unit_id": [r[0] for r in rows],
            "windpark": [r[1] for r in rows],
            "lat": [r[2] for r in rows],
            "lon": [r[3] for r in rows],
            "kind": "sea",
            "in_zone": True,
            "status": "In Betrieb",
            "net_mw": 5.0,
        }
    )
    s = sea_clusters(u)
    assert s.height == 2
    assert sorted(s["n_units"].to_list()) == [3, 6]
    assert in_domain(s).is_empty()


def test_in_domain_detects_outside_centroids() -> None:
    df = pl.DataFrame({"lat": [53.0, 57.0, 52.0], "lon": [12.0, 12.0, 17.0]})
    assert in_domain(df).height == 2


def test_centroids_yaml_readable_by_load_points(tmp_path: Path) -> None:
    clusters = pl.DataFrame(
        {
            "cluster_id": ["bb_01", "sea_baltic_1"],
            "lat": [53.2, 54.6],
            "lon": [12.1, 12.65],
            "kind": ["land", "sea"],
            "capacity_mw": [2389.23, 48.3],
            "n_units": [10, 2],
            "note": ["Tierra, Thüringen: prueba", "Mar (Báltico)"],
        }
    )
    path = tmp_path / "centroids.yaml"
    write_centroids(clusters, {"k_land": 1}, path)
    pts = load_points(path)
    assert [(p.id, p.kind) for p in pts] == [("bb_01", "land"), ("sea_baltic_1", "sea")]
    assert pts[0].lat == pytest.approx(53.2)
    assert path.read_text().isascii()
    assert "provisional: false" in path.read_text()


def test_resolve_coordinates_proxy_chain() -> None:
    def row(
        uid: str, lat: float | None, lon: float | None, plz: str, gem: str, kreis: str
    ) -> dict[str, object]:
        return {
            "unit_id": uid, "lat_raw": lat, "lon_raw": lon, "postal_code": plz,
            "municipality_key": gem, "landkreis": kreis, "bundesland": "Brandenburg",
            "kind": "land", "net_mw": 2.0,
        }  # fmt: skip

    df = pl.DataFrame(
        [
            row("ok1", 52.0, 13.0, "14000", "G1", "K1"),
            row("ok2", 52.2, 13.2, "14000", "G1", "K1"),
            row("swap", 13.0, 52.0, "99999", "G9", "K9"),  # ejes cambiados
            row("plz", None, None, "14000", "G2", "K2"),
            row("gem", None, None, "00000", "G1", "K2"),
            row("kreis", None, None, "00001", "G3", "K1"),
            row("bl", None, None, "00002", "G4", "K4"),
            row("zero", 0.0, 0.0, "14000", "G5", "K5"),  # coordenada absurda
        ]
    )
    out = resolve_coordinates(df).sort("unit_id")
    src = dict(zip(out["unit_id"], out["coord_source"], strict=True))
    assert src == {
        "ok1": "unit", "ok2": "unit", "swap": "unit_swapped", "plz": "plz",
        "gem": "gemeinde", "kreis": "landkreis", "bl": "bundesland", "zero": "plz",
    }  # fmt: skip
    plz = out.filter(pl.col("unit_id") == "plz").row(0, named=True)
    assert plz["lat"] == pytest.approx(52.1)
    assert plz["lon"] == pytest.approx(13.1)
    sw = out.filter(pl.col("unit_id") == "swap").row(0, named=True)
    assert (sw["lat"], sw["lon"]) == (52.0, 13.0)


def wind_raw(rows: list[dict[str, str | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [dict.fromkeys(WIND_FIELDS) | r for r in rows], schema=dict.fromkeys(WIND_FIELDS, pl.String)
    )


def test_typed_units_catalogs_kw_and_planned() -> None:
    cat = {"35": "In Betrieb", "31": "In Planung", "888": "Windenergie an Land",
           "889": "Windenergie auf See", "1400": "Brandenburg", "1416": "AWZ",
           "639": "Ostsee", "1000001": "50Hertz"}  # fmt: skip
    common = {"Breitengrad": "52.5", "Laengengrad": "13.5", "Inbetriebnahmedatum": "2020-05-17"}
    raw = wind_raw(
        [
            {"EinheitMastrNummer": "A", "LokationMaStRNummer": "L1", "EinheitBetriebsstatus": "35",
             "WindAnLandOderAufSee": "888", "Bundesland": "1400", "Bruttoleistung": "3200",
             "Nettonennleistung": "3000", **common},
            {"EinheitMastrNummer": "B", "LokationMaStRNummer": "L2", "EinheitBetriebsstatus": "31",
             "WindAnLandOderAufSee": "888", "Bundesland": "1400"},
            {"EinheitMastrNummer": "C", "LokationMaStRNummer": "L3", "EinheitBetriebsstatus": "35",
             "WindAnLandOderAufSee": "889", "Seelage": "639", "Bundesland": "1416",
             "Bruttoleistung": "8000", "Nettonennleistung": "8000", **common},
        ]
    )  # fmt: skip
    nap = pl.DataFrame(
        {"location": ["L3"], "nap_id": ["N"], "regelzone_id": ["1000001"]},
    )
    u = typed_units(raw, cat, nap).sort("unit_id")
    assert u["unit_id"].to_list() == ["A", "C"]  # la unidad "en planificación" se descarta
    assert u["net_mw"].to_list() == [3.0, 8.0]
    assert u["gross_mw"].to_list() == [3.2, 8.0]
    assert u["kind"].to_list() == ["land", "sea"]
    assert u["start_date"].to_list() == [D(2020, 5, 17)] * 2
    assert u["in_zone"].to_list() == [True, True]
    assert u["zone_rule"].to_list() == ["bundesland", "netzanschlusspunkt"]


def test_units_schema_flags_bad_rows() -> None:
    good = {
        "unit_id": ["A", "B"], "kind": ["land", "land"], "status": ["In Betrieb"] * 2,
        "lat": [52.0, 52.5], "lon": [13.0, 13.5], "coord_source": ["unit", "unit"],
        "net_mw": [3.0, 4.0], "gross_mw": [3.0, 4.0],
        "start_date": [D(2020, 1, 1), D(2021, 1, 1)], "end_date": [None, None],
    }  # fmt: skip
    schema = validate.units_schema(D(2026, 10, 15))
    ov = {"end_date": pl.Date}
    assert not validate.schema_issues(schema, pl.DataFrame(good, schema_overrides=ov))
    bad = good | {"net_mw": [3.0, 25.0], "unit_id": ["A", "A"], "lon": [13.0, 20.0]}
    checks = {
        i["check"] for i in validate.schema_issues(schema, pl.DataFrame(bad, schema_overrides=ov))
    }
    assert "unique_unit_id" in checks
    assert any("in_range" in c for c in checks)  # potencia ≥ 20 MW y longitud fuera del dominio
    inverted = good | {"end_date": [D(2019, 1, 1), None]}
    checks = {
        i["check"]
        for i in validate.schema_issues(schema, pl.DataFrame(inverted, schema_overrides=ov))
    }
    assert "end_not_before_start" in checks


def test_xml_stream_utf16_chunks_and_members(tmp_path: Path) -> None:
    body = "".join(
        f"<EinheitWind><EinheitMastrNummer>SEE{i}</EinheitMastrNummer>"
        f"<NameWindpark>Süd &amp; Nord {i}</NameWindpark><Vacio></Vacio></EinheitWind>"
        for i in range(5)
    )
    xml = f"<?xml version='1.0' encoding='UTF-16'?><EinheitenWind>{body}</EinheitenWind>"
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("EinheitenWind.xml", xml.encode("utf-16"))
        zf.writestr("Lokationen_10.xml", b"")
        zf.writestr("Lokationen_2.xml", b"")
    with zipfile.ZipFile(path) as zf:
        recs = list(iter_records(zf, "EinheitenWind.xml", "EinheitWind"))
        assert members(zf, "Lokationen") == ["Lokationen_2.xml", "Lokationen_10.xml"]
    assert [r["EinheitMastrNummer"] for r in recs] == [f"SEE{i}" for i in range(5)]
    assert recs[0]["NameWindpark"] == "Süd & Nord 0"
    assert "Vacio" not in recs[0]


def test_compare_entsoe_conventions() -> None:
    days = pl.date_range(D(2024, 12, 30), D(2026, 1, 2), "1d", eager=True)
    cap = pl.DataFrame({"date": days}).with_columns(
        land=pl.when(pl.col("date") < D(2025, 1, 1)).then(100.0).otherwise(110.0), sea=1.0
    )
    total = cap.unpivot(index="date", variable_name="kind", value_name="capacity_mw").with_columns(
        n_units=pl.lit(1, dtype=pl.Int64)
    )
    ent = pl.DataFrame({"year": [2025], "psr_type": ["B19"], "installed_mw": [104.0]})
    row = compare.capacity_vs_entsoe(total, ent)[0]
    assert row["mastr_jan1_mw"] == 110.0
    assert row["dev_jan1_pct"] == pytest.approx(100 * 6 / 104)
    gen = pl.DataFrame(
        {
            "ts_utc": [dt.datetime(2025, 3, 1, h, tzinfo=dt.UTC) for h in range(3)],
            "wind_offshore_mw": [0.0, 0.0, 0.0],
            "wind_onshore_mw": [50.0, 99.0, 121.0],
        }
    )
    g = next(r for r in compare.generation_vs_capacity(gen, total) if r["kind"] == "land")
    assert g["max_ratio"] == pytest.approx(121 / 110)
    assert g["steps_over_capacity"] == 1


def test_to_km_roundtrip_scale() -> None:
    xy = to_km(np.array([53.0, 54.0]), np.array([10.0, 10.0]))
    assert xy[1, 1] - xy[0, 1] == pytest.approx(111.195, rel=1e-3)
