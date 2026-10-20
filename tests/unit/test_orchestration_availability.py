# ruff: noqa: PLR2004
"""Disponibilidad NWP (F1.5): pasada completa con snapshots simulados, sondeo e idempotencia."""

import datetime as dt
from pathlib import Path

import httpx
import polars as pl
import pytest

from predico50hz.features.timing import closure_utc
from predico50hz.orchestration import availability as av
from predico50hz.orchestration.settings import OpsSettings

UTC = dt.UTC
DAY = dt.date(2026, 10, 16)


def at(h: int, m: int = 0, s: int = 0, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m, s), tzinfo=UTC)


class FakeSource:
    """Snapshots simulados; `complete` dice en qué ids el chunk sonda ya no es NaN."""

    def __init__(self, snaps: list[av.SnapshotInfo], complete: set[str]) -> None:
        self.snaps, self.complete = snaps, complete
        self.checked: list[str] = []

    def snapshots_since(self, since: dt.datetime) -> list[av.SnapshotInfo]:
        return [s for s in self.snaps if s.written_at >= since]

    def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
        self.checked.append(snapshot_id)
        return snapshot_id in self.complete


def burst(day: dt.date = DAY, final: bool = True) -> list[av.SnapshotInfo]:
    out = [av.SnapshotInfo(f"E{day}", "Expand dataset", at(8, 5, 14, day))]
    out += [
        av.SnapshotInfo(f"W{k}{day}", f"Update worker {k} at x", at(8, 10 + k // 10, k, day))
        for k in range(38)
    ]
    if final:
        out.append(av.SnapshotInfo(f"F{day}", "Update at 2026", at(8, 18, 58, day)))
    return out


def test_complete_pass_is_final_snapshot() -> None:
    src = FakeSource(burst(), {f"F{DAY}"})
    det = av.find_complete_snapshot(src, DAY)
    assert det is not None
    assert det.snapshot_id == f"F{DAY}"
    assert det.available_at == at(8, 18, 58)
    assert src.checked == [f"F{DAY}"]  # solo se lee el chunk del snapshot final, no los 38 workers


def test_not_complete_while_burst_is_running() -> None:
    assert av.find_complete_snapshot(FakeSource(burst(final=False), set()), DAY) is None
    assert av.find_complete_snapshot(FakeSource([], set()), DAY) is None


def test_final_with_nan_chunk_is_skipped_and_next_final_wins() -> None:
    snaps = [*burst(), av.SnapshotInfo("F2", "Update at 2026b", at(12, 4))]
    det = av.find_complete_snapshot(FakeSource(snaps, {"F2"}), DAY)
    assert det is not None
    assert det.snapshot_id == "F2"


def test_previous_day_final_does_not_count_before_todays_expand() -> None:
    prev = dt.date(2026, 10, 15)
    # `Update at` de ayer más tarde que las 00:00 de hoy (reproceso nocturno) y sin Expand hoy
    snaps = [*burst(prev), av.SnapshotInfo("N", "Update at night", at(0, 30))]
    assert av.find_complete_snapshot(FakeSource(snaps, {"N", f"F{prev}"}), DAY) is None


def make_settings(tmp_path: Path, **kw: object) -> OpsSettings:
    return OpsSettings(data_dir=tmp_path, nwp_bronze=tmp_path / "bronze", **kw)  # pyright: ignore


class Clock:
    def __init__(self, t: dt.datetime) -> None:
        self.t, self.sleeps = t, []  # pyright: ignore[reportUnknownMemberType]

    def now(self) -> dt.datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)  # pyright: ignore[reportUnknownMemberType]
        self.t += dt.timedelta(seconds=s)


class GrowingSource(FakeSource):
    """Se vuelve completa tras `ready_after` consultas a `snapshots_since`."""

    def __init__(self, ready_after: int) -> None:
        super().__init__(burst(), {f"F{DAY}"})
        self.calls, self.ready_after = 0, ready_after

    def snapshots_since(self, since: dt.datetime) -> list[av.SnapshotInfo]:
        self.calls += 1
        return super().snapshots_since(since) if self.calls > self.ready_after else []


def test_poll_waits_until_complete(tmp_path: Path) -> None:
    clock = Clock(at(7, 45))
    res = av.poll_until_available(
        make_settings(tmp_path, poll_interval_s=300),
        DAY,
        GrowingSource(ready_after=3),
        now=clock.now,
        sleep=clock.sleep,
        enfo_probe=lambda _d: at(7, 41, 26),
    )
    assert res.detection is not None
    assert res.polls == 4
    assert res.detected_at == at(8, 0)  # 07:45 + 3 espera de 5 min
    assert clock.sleeps == [300, 300, 300]
    assert res.enfo_available_at == at(7, 41, 26)


def test_poll_gives_up_at_deadline_with_one_attempt_at_least(tmp_path: Path) -> None:
    s = make_settings(tmp_path, poll_interval_s=300, poll_deadline_utc=dt.time(8, 0))
    clock = Clock(at(7, 50))
    res = av.poll_until_available(
        s, DAY, FakeSource([], set()), now=clock.now, sleep=clock.sleep, enfo_probe=lambda _d: None
    )
    assert res.detection is None
    assert clock.sleeps == [300, 300]  # 07:50 -> 07:55 -> 08:00 (límite)
    # tarde: arranca después del límite y aun así hace un intento
    late = Clock(at(13, 0))
    res = av.poll_until_available(
        s, DAY, FakeSource([], set()), now=late.now, sleep=late.sleep, enfo_probe=lambda _d: None
    )
    assert res.polls == 1
    assert late.sleeps == []


def test_enfo_network_error_is_only_a_note(tmp_path: Path) -> None:
    def boom(_d: dt.date) -> dt.datetime | None:
        raise httpx.ConnectError("sin red")

    clock = Clock(at(8, 30))
    res = av.poll_until_available(
        make_settings(tmp_path),
        DAY,
        FakeSource(burst(), {f"F{DAY}"}),
        now=clock.now,
        sleep=clock.sleep,
        enfo_probe=boom,
    )
    assert res.detection is not None
    assert res.enfo_available_at is None
    assert res.notes == ["enfo: ConnectError"]


def test_enfo_url() -> None:
    url = av.enfo_index_url("https://x/", DAY, 54)
    assert url == "https://x/20261016/00z/ifs/0p25/enfo/20261016000000-54h-enfo-ef.index"


def test_availability_table_keeps_first_detection(tmp_path: Path) -> None:
    s = make_settings(tmp_path)
    assert av.read_availability(s).is_empty()
    first = av.record_availability(
        s, DAY, av.SOURCE_DYNAMICAL, at(8, 18, 58), "F1", {"polls": 3}, at(8, 20)
    )
    again = av.record_availability(
        s, DAY, av.SOURCE_DYNAMICAL, at(12, 4), "F2", {"polls": 1}, at(13, 0)
    )
    other = av.record_availability(s, DAY, av.SOURCE_ENFO, at(7, 41, 26), "k", {}, at(7, 45))
    assert (first, again, other) == (True, False, True)
    df = av.read_availability(s).sort("source")
    assert df.height == 2
    dyn = df.filter(pl.col("source") == av.SOURCE_DYNAMICAL).row(0, named=True)
    assert dyn["source_ref"] == "F1"
    assert dyn["available_at"] == at(8, 18, 58)
    assert dyn["detected_at"] == at(8, 20)
    assert dyn["closure_utc"] == closure_utc(DAY) == at(13)
    assert dyn["margin_min"] == pytest.approx((13 * 60) - (8 * 60 + 18 + 58 / 60))
