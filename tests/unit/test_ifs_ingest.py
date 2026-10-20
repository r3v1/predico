# ruff: noqa: PLR2004, PT018, PT011
import datetime as dt
import json

import numpy as np
import pytest

from predico50hz.ingest.ifs import paths
from predico50hz.ingest.ifs.crop import Domain, crop_axes
from predico50hz.ingest.ifs.download import (
    ByteRange,
    IndexEntry,
    merge_ranges,
    parse_index,
    plan_step,
    select_entries,
    split_range,
)
from predico50hz.ingest.ifs.extract import Point, bilinear
from predico50hz.ingest.ifs.fields import DYNAMIC_FIELDS


def _line(param: str, levtype: str, offset: int, length: int, level: str | None = None) -> str:
    d: dict[str, object] = {
        "param": param,
        "levtype": levtype,
        "step": "18",
        "_offset": offset,
        "_length": length,
    }
    if level:
        d["levelist"] = level
    return json.dumps(d)


def test_parse_index_and_levels() -> None:
    text = "\n".join([_line("st", "sfc", 0, 10), _line("u", "pl", 10, 5, "925"), ""])
    got = parse_index(text)
    assert got == [
        IndexEntry("st", "sfc", None, 18, 0, 10),
        IndexEntry("u", "pl", 925, 18, 10, 5),
    ]


def test_select_reports_missing_fields() -> None:
    entries = [IndexEntry("10u", "sfc", None, 18, 0, 4), IndexEntry("u", "pl", 850, 18, 4, 4)]
    chosen, missing = select_entries(entries, DYNAMIC_FIELDS)
    assert [e.param for e in chosen] == ["10u", "u"]
    assert "100u" in missing and "10fg" in missing and "u@850" not in missing
    assert len(missing) == len(DYNAMIC_FIELDS) - 2


def test_merge_ranges_contiguous_and_gaps() -> None:
    a = IndexEntry("a", "sfc", None, 18, 0, 10)
    b = IndexEntry("b", "sfc", None, 18, 10, 5)  # contiguo con a
    c = IndexEntry("c", "sfc", None, 18, 20, 5)  # hueco de 5 bytes
    rs = merge_ranges([c, a, b])
    assert [(r.start, r.end) for r in rs] == [(0, 14), (20, 24)]
    assert [len(r.entries) for r in rs] == [2, 1]
    rs = merge_ranges([c, a, b], max_gap=5)
    assert [(r.start, r.end) for r in rs] == [(0, 24)]


def test_plan_step_excludes_lsm_unless_requested() -> None:
    entries = [IndexEntry("lsm", "sfc", None, 0, 0, 4), IndexEntry("10u", "sfc", None, 0, 4, 4)]
    rngs, _ = plan_step(entries, include_lsm=False)
    assert sum(len(r.entries) for r in rngs) == 1
    rngs, _ = plan_step(entries, include_lsm=True)
    assert sum(len(r.entries) for r in rngs) == 2


def test_split_range_validates_grib_markers() -> None:
    e1 = IndexEntry("a", "sfc", None, 0, 100, 12)
    e2 = IndexEntry("b", "sfc", None, 0, 112, 12)
    msg = b"GRIB" + b"x" * 4 + b"7777"
    rng = ByteRange(100, 123, (e1, e2))
    parts = split_range(rng, msg + msg)
    assert [p[0].param for p in parts] == ["a", "b"] and parts[1][1] == msg
    with pytest.raises(ValueError):
        split_range(rng, msg + b"y" * 12)
    with pytest.raises(ValueError):
        split_range(rng, msg)


@pytest.mark.parametrize(
    ("day", "cycle", "existing", "expected"),
    [
        # 2023: solo 0p4-beta, sin prefijo ifs/
        (dt.date(2023, 1, 18), 0, "20230118/00z/0p4-beta/oper/", ("0p4-beta", "oper")),
        # 2024-01-31 (ultimo dia sin 0p25)
        (dt.date(2024, 1, 31), 0, "20240131/00z/0p4-beta/oper/", ("0p4-beta", "oper")),
        # 2024-05-15 conviven ambas con prefijo ifs/: se prefiere 0p25
        (dt.date(2024, 5, 15), 0, "20240515/00z/ifs/0p25/oper/", ("0p25", "oper")),
        # 2026: ifs/0p25/oper
        (dt.date(2026, 10, 15), 0, "20261015/00z/ifs/0p25/oper/", ("0p25", "oper")),
        # 06Z: scda en 2023-24, oper en 2026
        (dt.date(2024, 5, 15), 6, "20240515/06z/ifs/0p25/scda/", ("0p25", "scda")),
        (dt.date(2026, 10, 15), 6, "20261015/06z/ifs/0p25/oper/", ("0p25", "oper")),
    ],
)
def test_resolve_layout(day: dt.date, cycle: int, existing: str, expected: tuple[str, str]) -> None:
    def exists(key: str) -> bool:
        return key.startswith(existing)

    lay = paths.resolve_layout(day, cycle, 18, exists)
    assert lay is not None and (lay.res, lay.stream) == expected
    assert lay.prefix == existing
    assert paths.index_key(lay, day, cycle, 18).endswith(
        f"{day:%Y%m%d}{cycle:02d}0000-18h-{expected[1]}-fc.index"
    )


def test_resolve_prefers_0p25_when_both_exist() -> None:
    both = {"20240515/00z/ifs/0p25/oper/", "20240515/00z/ifs/0p4-beta/oper/"}
    lay = paths.resolve_layout(
        dt.date(2024, 5, 15), 0, 18, lambda k: any(k.startswith(p) for p in both)
    )
    assert lay is not None and lay.res == "0p25"
    assert paths.resolve_layout(dt.date(2023, 1, 18), 0, 18, lambda _: False) is None


def test_crop_axes_ascending_and_inclusive() -> None:
    lats = np.arange(90, -90.1, -0.25)
    lons = np.arange(-180, 180, 0.25)
    ilat, ilon = crop_axes(lats, lons, Domain())
    assert lats[ilat][0] == 49.5 and lats[ilat][-1] == 56.0 and len(ilat) == 27
    assert lons[ilon][0] == 8.5 and lons[ilon][-1] == 16.0 and len(ilon) == 31


@pytest.mark.parametrize("step", [0.25, 0.4])
def test_bilinear_reproduces_linear_field_on_any_resolution(step: float) -> None:
    lats = np.arange(49.6, 56.0 + 1e-9, step)
    lons = np.arange(8.8, 16.0 + 1e-9, step)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    f = (2.0 * la + 3.0 * lo + 1.0).astype(np.float32)
    stack = np.stack([f, 2 * f])  # dim extra (p. ej. nivel)
    p = Point("x", 53.37, 13.91)
    got = bilinear(stack, lats, lons, p.lat, p.lon)
    expected = 2.0 * p.lat + 3.0 * p.lon + 1.0
    np.testing.assert_allclose(got, [expected, 2 * expected], rtol=1e-5)


def test_bilinear_exact_known_cell_and_bounds() -> None:
    lats = np.array([0.0, 1.0])
    lons = np.array([0.0, 1.0])
    f = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    assert float(bilinear(f, lats, lons, 0.5, 0.5)) == pytest.approx(15.0)
    assert float(bilinear(f, lats, lons, 0.25, 0.75)) == pytest.approx(12.5)
    assert float(bilinear(f, lats, lons, 1.0, 1.0)) == pytest.approx(30.0)
    with pytest.raises(ValueError):
        bilinear(f, lats, lons, 1.5, 0.5)
