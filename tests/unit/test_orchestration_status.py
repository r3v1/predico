# ruff: noqa: PLR2004
"""Estado y puerta G0: rachas, fallos, reintentos, días sin ejecución y hoy pendiente."""

import datetime as dt
from pathlib import Path

from predico50hz.orchestration import availability as av
from predico50hz.orchestration import status
from predico50hz.orchestration.runlog import read_records, record_unit_failure
from predico50hz.orchestration.settings import OpsSettings

TODAY = dt.date(2026, 10, 20)
UTC = dt.UTC


def run(job: str, day: dt.date, st: str = "ok") -> dict[str, str]:
    return {"job": job, "day": day.isoformat(), "status": st, "started_at": f"{day}T08:00:00+00:00"}


def clean_days(n: int, end: dt.date = TODAY) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for k in range(n):
        d = end - dt.timedelta(days=k)
        out += [run("nwp-daily", d), run("entsoe-daily", d)]
    return out


def test_seven_clean_days_pass() -> None:
    g = status.g0(clean_days(7), TODAY)
    assert g == {"streak_days": 7, "needed": 7, "passed": True}


def test_six_days_not_enough() -> None:
    g = status.g0(clean_days(6), TODAY)
    assert (g["streak_days"], g["passed"]) == (6, False)


def test_today_pending_does_not_break_streak() -> None:
    runs = clean_days(7, end=TODAY - dt.timedelta(days=1))
    g = status.g0(runs, TODAY)
    assert g["streak_days"] == 7
    # pero hoy a medias (solo una de las dos) tampoco rompe; hoy fallido sí
    runs2 = [*runs, run("entsoe-daily", TODAY)]
    assert status.g0(runs2, TODAY)["streak_days"] == 7
    runs3 = [*runs, run("nwp-daily", TODAY, "error")]
    assert status.g0(runs3, TODAY)["streak_days"] == 0


def test_failure_breaks_streak_even_if_retried() -> None:
    runs = clean_days(10)
    d = TODAY - dt.timedelta(days=2)
    runs.append(run("nwp-daily", d, "error"))  # fallo y luego reintento ok (ya está en runs)
    assert status.day_state(runs, "nwp-daily", d, TODAY) == "recovered"
    assert status.g0(runs, TODAY)["streak_days"] == 2


def test_onfailure_record_counts_as_failure() -> None:
    runs = [*clean_days(7), run("entsoe-daily", TODAY - dt.timedelta(days=1), "unit_failed")]
    assert status.g0(runs, TODAY)["streak_days"] == 1


def test_day_without_run_is_missing_and_breaks() -> None:
    runs = [r for r in clean_days(7) if r["day"] != "2026-10-18"]
    assert status.day_state(runs, "nwp-daily", dt.date(2026, 10, 18), TODAY) == "missing"
    assert status.g0(runs, TODAY)["streak_days"] == 2


def test_days_before_first_run_are_na_and_empty_log() -> None:
    runs = clean_days(2)
    assert status.day_state(runs, "nwp-daily", TODAY - dt.timedelta(days=5), TODAY) == "n/a"
    assert status.g0([], TODAY) == {"streak_days": 0, "needed": 7, "passed": False}


def test_build_report_with_availability(tmp_path: Path) -> None:
    s = OpsSettings(data_dir=tmp_path)
    now = dt.datetime(2026, 10, 16, 16, 0, tzinfo=UTC)
    d = now.date()
    av.record_availability(
        s, d, av.SOURCE_DYNAMICAL, dt.datetime(2026, 10, 16, 8, 18, 58, tzinfo=UTC), "F", {}, now
    )
    record_unit_failure(s, "predico-nwp-daily.service", now)
    rep = status.build(s, 3, now)
    row = rep["days"][0]
    assert row["dynamical"]["margin_min"] == 281.0  # 13:00 - 08:19
    assert row["ecmwf_enfo"] is None
    assert row["nwp-daily"] == "failed"
    assert rep["last_failures"][0]["status"] == "unit_failed"
    assert "G0" in status.render(rep)
    assert read_records(s.run_log_path)[0]["source"] == "onfailure"
