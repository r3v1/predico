"""El motivo de un fallo del CLI (escrito en stdout) llega a `runs.jsonl` y a `status`."""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from predico50hz.orchestration import jobs, status
from predico50hz.orchestration.runlog import FAILED, read_records
from predico50hz.orchestration.settings import OpsSettings

TOKEN = "SECRETO-1234-ABCD"  # gitleaks:allow (token falso de prueba)


def failing_cli(argv: list[str]) -> int:
    info = {"errors": [f"prices: HTTP 400: Bad Request securityToken={TOKEN}&x=1"]}
    print(json.dumps(info))
    print("prices: schema_ok=True filas=0")
    print("traza sin interés", file=sys.stderr)
    return 1


def test_run_cli_includes_stdout_reason_without_token() -> None:
    with pytest.raises(jobs.JobError) as ei:
        jobs._run_cli(failing_cli, [], "entsoe prices")  # pyright: ignore[reportPrivateUsage]
    msg = str(ei.value)
    assert "HTTP 400" in msg
    assert TOKEN not in msg


def test_reason_reaches_runs_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = OpsSettings(data_dir=tmp_path)
    monkeypatch.setattr("predico50hz.ingest.entsoe.__main__.main", failing_cli)
    with pytest.raises(jobs.JobError):
        jobs.entsoe_hourly(settings)
    failed = [r for r in read_records(settings.run_log_path) if r["status"] in FAILED]
    assert failed
    assert all("HTTP 400" in r["error"] and TOKEN not in r["error"] for r in failed)
    rep = status.build(settings, 3, dt.datetime.now(dt.UTC))
    out = status.render(rep)
    assert "HTTP 400" in out
    assert TOKEN not in out
