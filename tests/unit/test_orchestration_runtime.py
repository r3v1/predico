# ruff: noqa: PLR2004
"""Locks, configuración por entorno, secretos, planificador y jobs de ENTSO-E (sin red)."""

import datetime as dt
import json
import threading
from pathlib import Path

import pytest

from predico50hz.ingest.entsoe.redact import register_secret
from predico50hz.orchestration import availability as av
from predico50hz.orchestration import jobs, locks, scheduler
from predico50hz.orchestration.__main__ import build_parser
from predico50hz.orchestration.runlog import Run, read_records, record_unit_failure
from predico50hz.orchestration.settings import OpsSettings

UTC = dt.UTC
TOKEN = "t0ken-super-secreto-123"


# --- locks ---------------------------------------------------------------------------------
def test_lock_excludes_second_holder_and_releases(tmp_path: Path) -> None:
    with locks.file_lock(tmp_path, "x", timeout_s=1):
        with pytest.raises(locks.LockTimeoutError), locks.file_lock(tmp_path, "x", timeout_s=0.2):
            pass
        with locks.file_lock(tmp_path, "otro", timeout_s=0.2):  # otro nombre no choca
            pass
    with locks.file_lock(tmp_path, "x", timeout_s=0.2):  # liberado al salir
        pass


def test_lock_serializes_threads(tmp_path: Path) -> None:
    order: list[str] = []

    def worker(tag: str) -> None:
        with locks.file_lock(tmp_path, "w", timeout_s=5, poll_s=0.01):
            order.append(f"in-{tag}")
            threading.Event().wait(0.1)
            order.append(f"out-{tag}")

    ts = [threading.Thread(target=worker, args=(t,)) for t in "ab"]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert order[0][3:] == order[1][4:]  # in-a, out-a, in-b, out-b: nunca intercalados


# --- configuración -------------------------------------------------------------------------
def test_defaults_are_current_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in ("PREDICO_DATA_DIR", "PREDICO_NWP_BRONZE", "PREDICO_LOCK_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # sin .env
    s = OpsSettings()
    assert s.nwp_bronze == Path("/mnt/data/nwp/IFS-ENS/icechunk")
    assert s.ops_path == Path("data/ops")
    assert s.nwp_silver_path == Path("data/silver/ifs_ens_clusters")
    assert s.entsoe_bronze_path == Path("data/bronze/entsoe")
    assert s.centroids == Path("config/centroids.yaml")
    assert s.poll_interval_s == 300
    assert s.poll_deadline_utc == dt.time(12, 0)


def test_env_overrides_every_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREDICO_DATA_DIR", "/data")
    monkeypatch.setenv("PREDICO_NWP_BRONZE", "/nwp/icechunk")
    monkeypatch.setenv("PREDICO_CENTROIDS", "/cfg/centroids.yaml")
    monkeypatch.setenv("PREDICO_LOCK_DIR", "/run/locks")
    monkeypatch.setenv("PREDICO_RUN_LOG", "/var/log/runs.jsonl")
    monkeypatch.setenv("PREDICO_POLL_DEADLINE_UTC", "10:30")
    monkeypatch.setenv("PREDICO_POLL_INTERVAL_S", "60")
    s = OpsSettings()
    assert s.nwp_bronze == Path("/nwp/icechunk")
    assert s.ops_path == Path("/data/ops")
    assert s.lock_path == Path("/run/locks")
    assert s.run_log_path == Path("/var/log/runs.jsonl")
    assert s.availability_path == Path("/data/ops/nwp_availability")
    assert s.entsoe_silver_path == Path("/data/silver/entsoe")
    assert s.gold_path == Path("/data/gold/features")
    assert s.poll_deadline_utc == dt.time(10, 30)
    assert s.poll_interval_s == 60


def test_cli_has_all_jobs() -> None:
    p = build_parser()
    for job in ("nwp-daily", "entsoe-daily", "entsoe-hourly", "status", "publication-times"):
        assert p.parse_args([job]).job == job


# --- secretos ------------------------------------------------------------------------------
def test_run_log_never_contains_token(tmp_path: Path) -> None:
    register_secret(TOKEN)
    s = OpsSettings(data_dir=tmp_path)

    def failing() -> None:
        with Run(s, "entsoe-daily", dt.date(2026, 10, 16)) as r:
            r.details["nota"] = f"url https://x/api?securityToken={TOKEN}&a=1"
            with r.phase("all"):
                raise RuntimeError(f"GET https://web-api.tp.entsoe.eu/api?securityToken={TOKEN}")

    with pytest.raises(RuntimeError):
        failing()
    text = s.run_log_path.read_text()
    assert TOKEN not in text
    assert "<token-redactado>" in text
    rec = read_records(s.run_log_path)[0]
    assert rec["status"] == "error"
    assert rec["phases"][0]["status"] == "error"
    assert rec["phases"][0]["duration_s"] >= 0


# --- registro y OnFailure ------------------------------------------------------------------
def test_unit_failure_record(tmp_path: Path) -> None:
    s = OpsSettings(data_dir=tmp_path)
    record_unit_failure(
        s, "predico-entsoe-hourly.service", dt.datetime(2026, 10, 16, 9, tzinfo=UTC)
    )
    rec = read_records(s.run_log_path)[0]
    assert (rec["job"], rec["status"], rec["source"]) == (
        "entsoe-hourly",
        "unit_failed",
        "onfailure",
    )
    assert rec["day"] == "2026-10-16"


def test_corrupt_line_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "runs.jsonl"
    p.write_text('{"job": "a"}\nbasura\n{"job": "b"}\n')
    assert [r["job"] for r in read_records(p)] == ["a", "b"]


# --- jobs de ENTSO-E -----------------------------------------------------------------------
def fake_entsoe(calls: list[list[str]], fail: set[str]):
    def main(argv: list[str]) -> int:
        calls.append(argv)
        ds = argv[argv.index("--dataset") + 1]
        return 1 if ds in fail else 0

    return main


def test_entsoe_hourly_runs_both_datasets_and_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import predico50hz.ingest.entsoe.__main__ as em  # noqa: PLC0415

    calls: list[list[str]] = []
    monkeypatch.setattr(em, "main", fake_entsoe(calls, set()))
    s = OpsSettings(data_dir=tmp_path)
    jobs.entsoe_hourly(s)
    assert [c[c.index("--dataset") + 1] for c in calls] == ["prices", "forecast"]
    assert all(c[c.index("--days") + 1] == "2" for c in calls)
    assert str(tmp_path / "bronze" / "entsoe") in calls[0]
    rec = read_records(s.run_log_path)[0]
    assert (rec["job"], rec["status"]) == ("entsoe-hourly", "ok")
    assert [p["name"] for p in rec["phases"]] == ["prices", "forecast"]


def test_entsoe_partial_failure_still_tries_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import predico50hz.ingest.entsoe.__main__ as em  # noqa: PLC0415

    calls: list[list[str]] = []
    monkeypatch.setattr(em, "main", fake_entsoe(calls, {"prices"}))
    s = OpsSettings(data_dir=tmp_path)
    with pytest.raises(jobs.JobError, match="prices"):
        jobs.entsoe_hourly(s)
    assert len(calls) == 2
    rec = read_records(s.run_log_path)[0]
    assert rec["status"] == "error"
    assert [p["status"] for p in rec["phases"]] == ["error", "ok"]


def test_entsoe_daily_uses_dataset_all_3_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import predico50hz.ingest.entsoe.__main__ as em  # noqa: PLC0415

    calls: list[list[str]] = []
    monkeypatch.setattr(em, "main", fake_entsoe(calls, set()))
    jobs.entsoe_daily(OpsSettings(data_dir=tmp_path))
    assert calls[0][:4] == ["--dataset", "all", "--days", "3"]


def test_entsoe_job_waits_for_the_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import predico50hz.ingest.entsoe.__main__ as em  # noqa: PLC0415

    monkeypatch.setattr(em, "main", fake_entsoe([], set()))
    s = OpsSettings(data_dir=tmp_path, lock_timeout_s=0.2)
    with locks.file_lock(s.lock_path, locks.ENTSOE, 1), pytest.raises(jobs.JobError, match="lock"):
        jobs.entsoe_daily(s)
    assert read_records(s.run_log_path)[0]["status"] == "error"


def test_nwp_daily_fails_cleanly_when_pass_never_arrives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(av, "probe_enfo", lambda _s, _d: None)

    class Empty:
        def snapshots_since(self, since: dt.datetime) -> list[av.SnapshotInfo]:
            return []

        def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
            return False

    monkeypatch.setattr(jobs, "_is_ingested", lambda _s, _d: True)  # sin puesta al día
    s = OpsSettings(data_dir=tmp_path, nwp_bronze=tmp_path / "b")
    with pytest.raises(jobs.JobError, match="nwp-no-llega"):
        jobs.nwp_daily(s, dt.date(2020, 1, 1), source=Empty())
    rec = read_records(s.run_log_path)[0]
    assert (rec["job"], rec["status"], rec["day"]) == ("nwp-daily", "error", "2020-01-01")
    assert rec["phases"][0]["name"] == "poll"
    assert not (tmp_path / "b").exists()  # no tocó el bronce
    assert av.read_availability(s).is_empty()


# --- planificador --------------------------------------------------------------------------
def test_due_jobs() -> None:
    def t(h: int, m: int) -> dt.datetime:
        return dt.datetime(2026, 10, 16, h, m, tzinfo=UTC)

    assert scheduler.due_jobs(t(6, 0)) == ["entsoe-daily", "entsoe-hourly"]
    assert scheduler.due_jobs(t(7, 45)) == ["nwp-daily"]
    assert scheduler.due_jobs(t(9, 0)) == ["entsoe-hourly"]
    assert scheduler.due_jobs(t(9, 30)) == []


def test_catchup_only_missing_daily_jobs() -> None:
    now = dt.datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    runs = [{"job": "entsoe-daily", "day": "2026-10-16", "status": "ok"}]
    assert scheduler.catchup_jobs(now, runs) == ["nwp-daily"]
    early = dt.datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
    assert scheduler.catchup_jobs(early, []) == []


def test_serve_fires_each_minute_once(tmp_path: Path) -> None:
    t = [dt.datetime(2026, 10, 16, 5, 58, 30, tzinfo=UTC)]
    fired: list[tuple[str, str]] = []

    def now() -> dt.datetime:
        t[0] += dt.timedelta(seconds=45)
        return t[0]

    scheduler.serve(
        OpsSettings(data_dir=tmp_path),
        now=now,
        launch=lambda j: fired.append((j, t[0].strftime("%H:%M"))),
        max_ticks=10,
    )
    jobs_fired = [j for j, _ in fired]
    assert jobs_fired.count("entsoe-daily") == 1
    assert jobs_fired.count("entsoe-hourly") == 1


def test_availability_file_is_json_serializable(tmp_path: Path) -> None:
    s = OpsSettings(data_dir=tmp_path)
    av.record_availability(
        s,
        dt.date(2026, 10, 16),
        "x",
        dt.datetime(2026, 10, 16, 8, tzinfo=UTC),
        "r",
        {"a": 1},
        dt.datetime(2026, 10, 16, 9, tzinfo=UTC),
    )
    row = av.read_availability(s).row(0, named=True)
    assert json.loads(row["detail"]) == {"a": 1}


def test_vacuum_removes_old_silver_versions(tmp_path: Path) -> None:
    import polars as pl  # noqa: PLC0415

    s = OpsSettings(data_dir=tmp_path, vacuum_retention_h=0)
    table = s.entsoe_silver_path / "forecast"
    for k in range(3):  # tres versiones, cada una reescribe el fichero
        pl.DataFrame({"x": [k]}).write_delta(str(table), mode="overwrite")
    assert len(list(table.glob("*.parquet"))) == 3
    assert jobs.vacuum_silver(s) == {"forecast": 2}
    assert len(list(table.glob("*.parquet"))) == 1
    assert pl.read_delta(str(table))["x"].to_list() == [2]  # la versión actual intacta
