# ruff: noqa: PLR2004
"""Retraso de la generación real publicada (issue #11): último paso, retraso y cierre."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from predico50hz.features.timing import closure_utc
from predico50hz.ingest.entsoe.client import NoDataError
from predico50hz.orchestration import jobs
from predico50hz.orchestration import publication as pub
from predico50hz.orchestration.runlog import read_records
from predico50hz.orchestration.settings import OpsSettings

UTC = dt.UTC


def at(d: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(h, m), tzinfo=UTC)


def xml(start: dt.datetime, n: int, psr: str, res: str = "PT15M") -> bytes:
    step = dt.timedelta(hours=1) if res == "PT60M" else dt.timedelta(minutes=15)
    pts = "".join(
        f"<Point><position>{k + 1}</position><quantity>{10 + k}</quantity></Point>"
        for k in range(n)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
<revisionNumber>1</revisionNumber><createdDateTime>2026-01-01T00:00:00Z</createdDateTime>
<TimeSeries><curveType>A01</curveType><MktPSRType><psrType>{psr}</psrType></MktPSRType>
<Period><timeInterval><start>{start:%Y-%m-%dT%H:%MZ}</start>
<end>{start + n * step:%Y-%m-%dT%H:%MZ}</end></timeInterval>
<resolution>{res}</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>
""".encode()


class FakeClient:
    """Responde con n cuartos por tipo desde `start`; sin red."""

    def __init__(self, n: dict[str, int] | None, start: dt.datetime | None = None) -> None:
        self.n, self.start, self.closed = n, start, False

    def fetch(self, params: dict[str, str], start: dt.datetime, end: dt.datetime) -> bytes:
        psr = params["psrType"]
        if not self.n or not self.n.get(psr) or self.start is None:
            raise NoDataError("sin datos")
        return xml(self.start, self.n[psr], psr)

    def close(self) -> None:
        self.closed = True


def test_last_published_step_is_min_of_both_types() -> None:
    s = at(dt.date(2026, 3, 10), 0)
    got = pub.last_published_step([xml(s, 8, "B18"), xml(s, 6, "B19")])
    assert got["last_b18"] == "2026-03-10T01:45+00:00"
    assert got["last_b19"] == "2026-03-10T01:15+00:00"
    assert got["last_step"] == got["last_b19"]


def test_last_published_step_hourly_covers_four_quarters() -> None:
    s = at(dt.date(2026, 3, 10), 0)
    got = pub.last_published_step([xml(s, 2, "B18", "PT60M"), xml(s, 8, "B19")])
    assert got["last_b18"] == "2026-03-10T01:45+00:00"
    assert got["last_step"] == "2026-03-10T01:45+00:00"


def test_missing_type_gives_no_last_step() -> None:
    s = at(dt.date(2026, 3, 10), 0)
    got = pub.last_published_step([xml(s, 8, "B18")])
    assert got["last_step"] is None
    assert got["last_b19"] is None


def test_probe_no_data_yields_empty_observation() -> None:
    now = at(dt.date(2026, 3, 10), 9, 5)
    obs = pub.probe_generation(FakeClient(None), now)  # pyright: ignore[reportArgumentType]
    assert obs["last_step"] is None
    assert obs["observed_at"] == now.isoformat(timespec="seconds")


def test_lag_minutes() -> None:
    o = {"observed_at": "2026-03-10T09:05:00+00:00", "last_step": "2026-03-10T08:15+00:00"}
    assert pub.lag_minutes(o) == 50.0
    assert pub.lag_minutes({"observed_at": o["observed_at"], "last_step": None}) is None


def _obs(seen: dt.datetime, step: dt.datetime | None) -> dict[str, Any]:
    return {
        "observed_at": seen.isoformat(timespec="seconds"),
        "last_step": step.isoformat(timespec="minutes") if step else None,
    }


def test_report_per_closure_and_summary() -> None:
    d1, d2, d3 = dt.date(2026, 3, 10), dt.date(2026, 3, 11), dt.date(2026, 3, 12)
    c1, c2 = closure_utc(d1), closure_utc(d2)
    obs = [
        _obs(c1 - dt.timedelta(hours=1, minutes=5), c1 - dt.timedelta(hours=3)),  # no es la última
        _obs(c1 - dt.timedelta(minutes=5), c1 - dt.timedelta(minutes=90)),
        _obs(c1 + dt.timedelta(minutes=55), c1 + dt.timedelta(minutes=10)),  # tras el cierre
        _obs(c2 - dt.timedelta(minutes=10), c2 - dt.timedelta(minutes=130)),
    ]
    rep = pub.lag_report(obs, d1, at(d3, 20))
    by = {x["closure_day"]: x for x in rep["days"]}
    assert by["2026-03-10"]["lag_at_closure_min"] == 90.0
    assert by["2026-03-11"]["lag_at_closure_min"] == 130.0
    assert by["2026-03-12"]["status"] == "no_observation"
    assert rep["at_closure"] == {"n": 2, "median_min": 110.0, "max_min": 130.0}
    # retrasos por observación: 115, 85, 45, 120 min
    assert rep["per_observation"] == {"n": 4, "median_min": 100.0, "max_min": 120.0}


def test_report_ignores_stale_observation_and_null_step() -> None:
    d = dt.date(2026, 3, 10)
    c = closure_utc(d)
    rep = pub.lag_report([_obs(c - dt.timedelta(hours=5), c - dt.timedelta(hours=7))], d, at(d, 20))
    assert rep["days"][0]["status"] == "no_observation"
    rep = pub.lag_report([_obs(c - dt.timedelta(minutes=5), None)], d, at(d, 20))
    assert rep["days"][0]["status"] == "no_data"
    assert rep["at_closure"] == {"n": 0}


def test_observations_roundtrip_ignores_truncated_line(tmp_path: Path) -> None:
    p = tmp_path / "ops" / "generation_lag.jsonl"
    pub.append_observation(p, {"observed_at": "2026-03-10T09:00:00+00:00", "last_step": None})
    with p.open("a") as f:
        f.write('{"observed_at": "2026')
    assert len(pub.read_observations(p)) == 1


def test_compute_and_render_include_generation_lag(tmp_path: Path) -> None:
    d = dt.date(2026, 3, 10)
    c = closure_utc(d)
    p = tmp_path / "gen.jsonl"
    pub.append_observation(p, _obs(c - dt.timedelta(minutes=5), c - dt.timedelta(minutes=100)))
    res = pub.compute(tmp_path / "bronze", d, at(d, 20), None, p)
    assert res["generation_lag"]["at_closure"]["max_min"] == 100.0
    json.dumps(res)
    assert "generación real" in pub.render(res)
    assert "generation_lag" not in pub.compute(tmp_path / "bronze", d, at(d, 20), None)


def _patched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "_run_cli", lambda *a, **k: None)


def test_hourly_job_records_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    s = OpsSettings(data_dir=tmp_path)
    now = at(dt.date(2026, 3, 10), 9, 5)
    client = FakeClient({"B18": 30, "B19": 28}, at(dt.date(2026, 3, 10), 0))
    jobs.entsoe_hourly(s, now=now, client_factory=lambda: client)
    obs = pub.read_observations(s.generation_lag_path)
    assert obs[0]["last_step"] == "2026-03-10T06:45+00:00"
    assert client.closed
    rec = read_records(s.run_log_path)[-1]
    assert rec["status"] == "ok"
    assert rec["details"]["generation_last_step"] == "2026-03-10T06:45+00:00"


def test_hourly_job_survives_no_generation_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch)
    s = OpsSettings(data_dir=tmp_path)
    jobs.entsoe_hourly(s, now=at(dt.date(2026, 3, 10), 9), client_factory=lambda: FakeClient(None))
    assert pub.read_observations(s.generation_lag_path)[0]["last_step"] is None
    assert read_records(s.run_log_path)[-1]["status"] == "ok"


def test_hourly_job_survives_generation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch)
    s = OpsSettings(data_dir=tmp_path)

    def boom() -> Any:
        raise RuntimeError("caído")

    jobs.entsoe_hourly(s, now=at(dt.date(2026, 3, 10), 9), client_factory=boom)
    rec = read_records(s.run_log_path)[-1]
    assert rec["status"] == "ok"
    assert "caído" in rec["details"]["generation_error"]
