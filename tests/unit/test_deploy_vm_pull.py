"""Copia de la VM: unidades systemd y `pull-data.sh` sin red (rsync/ssh falsos en PATH)."""
# ruff: noqa: PLR2004

import configparser
import datetime as dt
import os
import subprocess
from pathlib import Path

import pytest

from predico50hz.orchestration import status
from predico50hz.orchestration.runlog import read_records, record_unit_failure
from predico50hz.orchestration.settings import OpsSettings

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
SCRIPT = ROOT / "deploy" / "hetzner" / "pull-data.sh"
TIMERS = sorted(SYSTEMD.glob("predico-*.timer"))
SERVICES = [p for p in sorted(SYSTEMD.glob("predico-*.service")) if "@" not in p.name]


def parse(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(path, encoding="utf-8")
    return cp


def test_vm_pull_units_exist() -> None:
    names = {p.name for p in SYSTEMD.iterdir()}
    assert {"predico-vm-pull.service", "predico-vm-pull.timer"} <= names


@pytest.mark.parametrize("timer", TIMERS, ids=lambda p: p.name)
def test_timers_have_required_properties(timer: Path) -> None:
    t = parse(timer)["Timer"]
    assert t["OnCalendar"].endswith(" UTC")
    assert t["Persistent"] == "true"
    assert t["AccuracySec"] == "1s"
    assert t["Unit"] == timer.name.replace(".timer", ".service")


@pytest.mark.parametrize("service", SERVICES, ids=lambda p: p.name)
def test_services_have_required_properties(service: Path) -> None:
    cp = parse(service)
    assert cp["Unit"]["OnFailure"] == "predico-failure@%n.service"
    assert cp["Service"]["Type"] == "oneshot"
    assert cp["Service"]["Restart"] == "on-failure"
    assert "StartLimitBurst" in cp["Unit"]
    assert "@REPO@" in cp["Service"]["WorkingDirectory"]


def test_install_script_lists_vm_pull() -> None:
    text = (SYSTEMD / "install.sh").read_text(encoding="utf-8")
    assert "predico-vm-pull.timer" in text
    assert "predico-vm-pull.service" in text


def test_vm_pull_service_runs_script() -> None:
    exec_start = parse(SYSTEMD / "predico-vm-pull.service")["Service"]["ExecStart"]
    assert exec_start.endswith("deploy/hetzner/pull-data.sh --with-nwp")


def test_record_failure_of_vm_pull_is_harmless(tmp_path: Path) -> None:
    s = OpsSettings(data_dir=tmp_path)
    record_unit_failure(s, "predico-vm-pull.service")
    assert read_records(s.run_log_path)[0]["job"] == "vm-pull"
    rep = status.build(s, 3, dt.datetime.now(dt.UTC))
    assert rep["g0"]["passed"] is False
    assert rep["last_failures"][0]["job"] == "vm-pull"
    assert status.render(rep)


def fake_bin(tmp: Path) -> tuple[Path, Path]:
    bindir = tmp / "bin"
    bindir.mkdir()
    calls = tmp / "calls.txt"
    for tool in ("rsync", "ssh"):
        f = bindir / tool
        f.write_text(f'#!/usr/bin/env bash\necho "{tool} $*" >> "{calls}"\n')
        f.chmod(0o755)
    return bindir, calls


def run_script(tmp: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir, calls = fake_bin(tmp)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    r = subprocess.run(
        [str(SCRIPT), "--dest", str(tmp / "dest"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = calls.read_text().splitlines() if calls.exists() else []
    return r, lines


def test_dry_run_uses_rsync_without_delete_and_with_excludes(tmp_path: Path) -> None:
    r, calls = run_script(tmp_path, "--dry-run", "--with-nwp")
    assert r.returncode == 0, r.stderr
    assert len(calls) == 2  # data/ y nwp/
    for c in calls:
        assert c.startswith("rsync ")
        assert "--delete" not in c
        assert "--dry-run" in c
        assert "BatchMode=yes" in c
        for ex in ("ops/locks/", "ops/export_*.tar.gz", "*.tmp", "__pycache__"):
            assert f"--exclude {ex}" in c
    assert "antuzanos:predico/data/" in calls[0]
    assert "antuzanos:predico/nwp/" in calls[1]
    assert not (tmp_path / "dest").exists()


def test_default_copies_only_data_and_writes_last_pull(tmp_path: Path) -> None:
    r, calls = run_script(tmp_path, "--host", "vm", "--remote-dir", "x")
    assert r.returncode == 0, r.stderr
    assert len(calls) == 1
    assert "vm:x/data/" in calls[0]
    assert "nwp" not in calls[0]
    last = (tmp_path / "dest" / "last_pull.json").read_text()
    assert '"status": "ok"' in last
    assert '"host": "vm"' in last


def test_rsync_failure_gives_nonzero_exit(tmp_path: Path) -> None:
    bindir, _ = fake_bin(tmp_path)
    (bindir / "rsync").write_text("#!/usr/bin/env bash\nexit 23\n")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    r = subprocess.run(
        [str(SCRIPT), "--dest", str(tmp_path / "dest")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 23
    assert '"status": "error"' in (tmp_path / "dest" / "last_pull.json").read_text()
