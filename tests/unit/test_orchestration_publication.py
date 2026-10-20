# ruff: noqa: PLR2004, PLR0913
"""Hora de publicación de ENTSO-E (F2.5): bronce sintético, días de cambio de hora y censura."""

import datetime as dt
import json
from pathlib import Path

from predico50hz.features.timing import closure_utc, target_grid
from predico50hz.ingest.entsoe import bronze
from predico50hz.orchestration import publication as pub

UTC = dt.UTC


def at(d: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(h, m), tzinfo=UTC)


def xml(grid: list[dt.datetime], psr: str, res: str = "PT15M") -> bytes:
    step = dt.timedelta(hours=1) if res == "PT60M" else dt.timedelta(minutes=15)
    pts = "".join(
        f"<Point><position>{k + 1}</position><quantity>{10 + k}</quantity></Point>"
        for k in range(len(grid))
    )
    mkt = f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>" if psr else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
<revisionNumber>1</revisionNumber><createdDateTime>2026-01-01T00:00:00Z</createdDateTime>
<TimeSeries><curveType>A01</curveType>{mkt}
<Period><timeInterval><start>{grid[0]:%Y-%m-%dT%H:%MZ}</start>
<end>{grid[-1] + step:%Y-%m-%dT%H:%MZ}</end></timeInterval>
<resolution>{res}</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>
""".encode()


def fetch(
    root: Path,
    dataset: str,
    when: dt.datetime,
    day: dt.date,
    *,
    complete: bool,
    psrs: tuple[str, ...] = ("B18", "B19"),
    cover_from: dt.date | None = None,
) -> None:
    """Una descarga: con el día objetivo de `day` completo, o con él ausente."""
    stamp = bronze.stamp(when)
    start_d = cover_from or day - dt.timedelta(days=2)
    bronze.write_run_info(
        root, dataset, stamp, {"start": start_d, "end": day + dt.timedelta(days=2)}
    )
    grid = target_grid(day)
    window_start = dt.datetime.combine(day, dt.time(0), tzinfo=UTC)
    for psr in psrs if dataset == "forecast" else ("",):
        if complete:
            raw = xml(grid, psr)
        else:  # hoy, sin el día siguiente
            raw = xml([window_start + dt.timedelta(minutes=15 * k) for k in range(8)], psr)
        bronze.write_chunk(root, dataset, stamp, psr, window_start, grid[-1], raw)


def test_price_measured_with_first_complete_fetch(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)  # invierno: 96 cuartos, cierre 14:00 UTC
    for h, ok in [(10, False), (11, False), (12, True), (13, True)]:
        fetch(tmp_path, "prices", at(d, h, 0), d, complete=ok)
    res = pub.compute(tmp_path, d, at(d, 20))
    day = res["datasets"]["prices"]["days"][0]
    assert day["status"] == "measured"
    assert day["first_complete_at"] == "2026-01-15T12:00:00+00:00"
    assert day["last_incomplete_at"] == "2026-01-15T11:00:00+00:00"
    assert day["margin_min"] == 120.0
    assert day["before_closure"] is True
    assert day["target_day"] == "2026-01-16"
    s = res["datasets"]["prices"]["summary"]
    assert (s["n_measured"], s["utc_time_median"], s["pct_before_closure"]) == (1, "12:00", 100.0)


def test_forecast_needs_both_b18_and_b19(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "forecast", at(d, 10), d, complete=False)
    fetch(tmp_path, "forecast", at(d, 16), d, complete=True, psrs=("B19",))  # falta B18
    fetch(tmp_path, "forecast", at(d, 17), d, complete=True)
    day = pub.compute(tmp_path, d, at(d, 20))["datasets"]["forecast"]["days"][0]
    assert day["first_complete_at"] == "2026-01-15T17:00:00+00:00"
    assert day["before_closure"] is False
    assert day["margin_min"] == -180.0
    s = pub.summarize([day])
    assert s["pct_before_closure"] == 0.0


def test_censored_when_already_complete_at_first_fetch(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "prices", at(d, 12), d, complete=True)
    fetch(tmp_path, "prices", at(d, 13), d, complete=True)
    res = pub.compute(tmp_path, d, at(d, 20))
    day = res["datasets"]["prices"]["days"][0]
    assert day["status"] == "censored"
    assert res["datasets"]["prices"]["summary"]["n_measured"] == 0


def test_fetch_that_did_not_request_the_day_is_ignored(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    # el primer sondeo pedía hasta hoy: no vio el día siguiente, no sirve como "incompleto"
    fetch(tmp_path, "prices", at(d, 9), d, complete=False, cover_from=d - dt.timedelta(days=9))
    bronze.write_run_info(tmp_path, "prices", bronze.stamp(at(d, 9)), {"start": d, "end": d})
    fetch(tmp_path, "prices", at(d, 12), d, complete=True)
    day = pub.compute(tmp_path, d, at(d, 20))["datasets"]["prices"]["days"][0]
    assert day["status"] == "censored"


def test_not_published_by_closure_and_no_fetches(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "forecast", at(d, 10), d, complete=False)
    fetch(tmp_path, "forecast", at(d, 16), d, complete=False)
    res = pub.compute(tmp_path, d - dt.timedelta(days=3), at(d, 20))["datasets"]["forecast"]
    # los cierres sin ninguna descarga (d-3, d-2) se omiten; d-1 y d las vieron incompletas
    assert [x["closure_day"] for x in res["days"]] == ["2026-01-14", "2026-01-15"]
    assert {x["status"] for x in res["days"]} == {"not_published"}
    assert res["summary"]["n_not_published_by_closure"] == 2


def test_dst_days_have_92_and_100_quarters(tmp_path: Path) -> None:
    for d, n in [(dt.date(2026, 3, 28), 92), (dt.date(2026, 10, 24), 100)]:
        assert len(target_grid(d)) == n
        fetch(tmp_path, "prices", at(d, 10), d, complete=False)
        fetch(tmp_path, "prices", at(d, 11), d, complete=True)
        res = pub.compute(tmp_path, d, at(d, 20))["datasets"]["prices"]["days"]
        mine = next(x for x in res if x["closure_day"] == d.isoformat())
        assert mine["status"] == "measured"
        assert mine["closure_utc"] == closure_utc(d).isoformat(timespec="minutes")


def test_hourly_price_rows_cover_four_quarters(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "prices", at(d, 10), d, complete=False)
    grid = target_grid(d)
    hours = grid[::4]
    stamp = bronze.stamp(at(d, 11))
    bronze.write_run_info(
        tmp_path,
        "prices",
        stamp,
        {"start": d - dt.timedelta(days=2), "end": d + dt.timedelta(days=2)},
    )
    bronze.write_chunk(tmp_path, "prices", stamp, "", grid[0], grid[-1], xml(hours, "", "PT60M"))
    day = pub.compute(tmp_path, d, at(d, 20))["datasets"]["prices"]["days"][0]
    assert day["status"] == "measured"


def test_cache_reuses_measured_days_and_json_roundtrip(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "prices", at(d, 11), d, complete=False)
    fetch(tmp_path, "prices", at(d, 12), d, complete=True)
    first = pub.compute(tmp_path, d, at(d, 20))
    prev = json.loads(json.dumps(first))
    prev["datasets"]["prices"]["days"][0]["margin_min"] = (
        999.0  # marca para detectar la reutilización
    )
    again = pub.compute(tmp_path, d, at(d, 21), prev)
    assert again["datasets"]["prices"]["days"][0]["margin_min"] == 999.0
    assert "COTA SUPERIOR" in pub.render(first)


def test_coarse_when_fetches_are_too_far_apart(tmp_path: Path) -> None:
    d = dt.date(2026, 1, 15)
    fetch(tmp_path, "forecast", at(d, 9), d, complete=False)
    fetch(tmp_path, "forecast", at(d, 13), d, complete=True)  # 4 h sin descargas: ventana burda
    res = pub.compute(tmp_path, d, at(d, 20))["datasets"]["forecast"]
    assert res["days"][0]["status"] == "coarse"
    assert res["days"][0]["gap_min"] == 240.0
    assert res["summary"]["n_measured"] == 0
    assert res["summary"]["n_coarse"] == 1
