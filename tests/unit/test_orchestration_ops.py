# ruff: noqa: PLR2004
"""Puesta al día de `nwp-daily`, host, política de días sin ejecución y exportación."""

import datetime as dt
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from predico50hz.ingest.entsoe.redact import register_secret
from predico50hz.orchestration import availability as av
from predico50hz.orchestration import export, jobs, status
from predico50hz.orchestration.runlog import read_records, record_unit_failure, tee_to_log
from predico50hz.orchestration.settings import OpsSettings

UTC = dt.UTC
TODAY = dt.date(2026, 10, 16)
TOKEN = "t0ken-super-secreto-123"


def at(day: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=UTC)


def burst(day: dt.date) -> list[av.SnapshotInfo]:
    return [
        av.SnapshotInfo(f"E{day}", "Expand dataset", at(day, 8, 5)),
        av.SnapshotInfo(f"F{day}", "Update at x", at(day, 8, 18)),
    ]


class Source:
    def __init__(self, days: list[dt.date]) -> None:
        self.snaps = [s for d in days for s in burst(d)]

    def snapshots_since(self, since: dt.datetime) -> list[av.SnapshotInfo]:
        return [s for s in self.snaps if s.written_at >= since]

    def is_complete(self, snapshot_id: str, day: dt.date) -> bool:
        return snapshot_id == f"F{day}"


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """`nwp_daily` con ingesta, validación y oro sustituidos: solo se prueba la orquestación."""
    calls: dict[str, Any] = {"ingest": [], "validate": [], "gold": [], "bronze": set()}

    def ingest(_s: OpsSettings, day: dt.date, snap: str) -> dict[str, Any]:
        calls["ingest"].append((day, snap))
        calls["bronze"].add(day)
        return {"day": day.isoformat(), "status": "ok"}

    monkeypatch.setattr(av, "probe_enfo", lambda _s, _d: at(TODAY, 7, 41))
    monkeypatch.setattr(jobs, "_is_ingested", lambda _s, d: d in calls["bronze"])
    monkeypatch.setattr(jobs, "_ingest_day", ingest)
    monkeypatch.setattr(
        jobs, "_validate", lambda _s, end, must: calls["validate"].append((end, must)) or {}
    )
    monkeypatch.setattr(jobs, "_gold", lambda _s, a, b: calls["gold"].append((a, b)))
    calls["settings"] = OpsSettings(
        data_dir=tmp_path, nwp_bronze=tmp_path / "b", catchup_days=5, host_id="vm-test",
        poll_deadline_utc=dt.time(0, 0),  # ya pasado: un único sondeo, sin dormir
    )  # fmt: skip
    return calls


def test_plan_catchup() -> None:
    have = {dt.date(2026, 10, 15), dt.date(2026, 10, 13)}
    assert jobs.plan_catchup(TODAY, 4, lambda d: d in have) == [
        dt.date(2026, 10, 12),
        dt.date(2026, 10, 14),
    ]


def test_catchup_recovers_missing_days_and_flags_them(flow: dict[str, Any]) -> None:
    s = flow["settings"]
    d2, d4 = TODAY - dt.timedelta(days=2), TODAY - dt.timedelta(days=4)
    flow["bronze"] |= {TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=3)}
    # dynamical tiene hoy, d2 y d4; d5 falta en dynamical (y en el bronce)
    r = jobs.nwp_daily(s, TODAY, source=Source([TODAY, d2, d4]))
    assert {d for d, _ in flow["ingest"]} == {TODAY, d2, d4}
    assert r["caught_up"] == 2
    assert flow["validate"][0][0] == TODAY
    assert flow["validate"][0][1] == {TODAY, d2, d4}
    assert flow["gold"] == [(d4, TODAY)]
    rows = av.read_availability(s).filter(pl.col("source") == av.SOURCE_DYNAMICAL)
    by_day = {r["day"]: r for r in rows.iter_rows(named=True)}
    assert set(by_day) == {TODAY, d2, d4}
    assert by_day[d2]["catch_up"] is True
    assert by_day[d2]["available_at"] == at(d2, 8, 18)  # hora real del commit, no la de detección
    assert by_day[d2]["host"] == "vm-test"
    assert by_day[TODAY]["catch_up"] is True  # detectada tras el límite de espera (00:00 aquí)
    rec = read_records(s.run_log_path)[0]
    assert rec["status"] == "ok"
    assert rec["host"] == "vm-test"
    cu = next(p for p in rec["phases"] if p["name"] == "catchup")
    assert cu["detail"]["unavailable"] == [(TODAY - dt.timedelta(days=5)).isoformat()]


def test_today_missing_fails_but_past_days_are_still_recovered(flow: dict[str, Any]) -> None:
    s = flow["settings"]
    d1 = TODAY - dt.timedelta(days=1)
    with pytest.raises(jobs.JobError, match="nwp-no-llega"):
        jobs.nwp_daily(s, TODAY, source=Source([d1]))
    assert [d for d, _ in flow["ingest"]] == [d1]
    assert flow["gold"] == [(d1, d1)]
    assert read_records(s.run_log_path)[0]["status"] == "error"


def test_late_pass_is_ingested_without_failing(flow: dict[str, Any]) -> None:
    """El portátil se enciende a las 18:00: la pasada de hoy ya está y no se falla por la hora."""
    s = flow["settings"]
    s.poll_deadline_utc = dt.time(12, 0)
    clock_now = dt.datetime.now(UTC).date()
    jobs.nwp_daily(s, clock_now, source=Source([clock_now]))
    assert [d for d, _ in flow["ingest"]] == [clock_now]
    assert read_records(s.run_log_path)[0]["status"] == "ok"


def test_validate_tolerates_uningested_previous_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import predico50hz.ingest.dynamical.validate as dv  # noqa: PLC0415

    def rep(gap_days: list[str]) -> dict[str, Any]:
        return {
            "coverage": 0.5,
            "gaps": [{"init_date": d, "causes": ["no_ingerida"]} for d in gap_days],
            "bronze_structure_problems": [],
        }

    s = OpsSettings(data_dir=tmp_path, validate_days=7)
    monkeypatch.setattr(dv, "run", lambda *a, **k: rep(["2026-10-10", "2026-10-11"]))
    out = jobs._validate(s, TODAY, {TODAY})  # pyright: ignore[reportPrivateUsage]
    assert out["gaps_tolerated"] == ["2026-10-10", "2026-10-11"]
    monkeypatch.setattr(dv, "run", lambda *a, **k: rep(["2026-10-16"]))
    with pytest.raises(jobs.JobError, match="2026-10-16"):
        jobs._validate(s, TODAY, {TODAY})  # pyright: ignore[reportPrivateUsage]


# --- host y tabla antigua ------------------------------------------------------------------
def test_host_in_records_and_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PREDICO_HOST_ID", "hetzner-1")
    s = OpsSettings(data_dir=tmp_path)
    assert s.host == "hetzner-1"
    record_unit_failure(s, "predico-nwp-daily.service")
    assert read_records(s.run_log_path)[0]["host"] == "hetzner-1"
    monkeypatch.delenv("PREDICO_HOST_ID")
    assert OpsSettings(data_dir=tmp_path, host_id=None).host  # hostname por defecto


def test_old_availability_table_is_migrated(tmp_path: Path) -> None:
    s = OpsSettings(data_dir=tmp_path, host_id="pc")
    old = pl.DataFrame(
        {
            "day": [TODAY],
            "source": ["dynamical"],
            "available_at": [at(TODAY, 8, 18)],
            "detected_at": [at(TODAY, 8, 20)],
            "source_ref": ["F"],
            "closure_utc": [at(TODAY, 13)],
            "margin_min": [281.0],
            "detail": ["{}"],
        },
        schema={k: v for k, v in av.SCHEMA.items() if k not in ("host", "catch_up")},
    )
    old.write_delta(str(s.availability_path))
    assert av.read_availability(s).row(0, named=True)["host"] == "pc"
    assert av.record_availability(s, TODAY, "ecmwf_enfo", at(TODAY, 7, 41), "k", {}, at(TODAY, 8))
    df = av.read_availability(s)
    assert df.height == 2
    assert set(df["host"]) == {"pc"}
    assert df["catch_up"].to_list() == [False, False]


# --- política de días sin ejecución --------------------------------------------------------
def run(job: str, day: dt.date) -> dict[str, str]:
    return {
        "job": job,
        "day": day.isoformat(),
        "status": "ok",
        "started_at": f"{day}T08:00:00+00:00",
    }


def runs_missing_one_day() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for k in range(1, 9):
        d = TODAY - dt.timedelta(days=k)
        if k == 3:
            continue  # el contenedor murió o el portátil estaba apagado
        out += [run("nwp-daily", d), run("entsoe-daily", d)]
    return out


def test_vm_counts_day_without_run_as_failure(tmp_path: Path) -> None:
    runs = runs_missing_one_day()
    d3 = TODAY - dt.timedelta(days=3)
    assert status.day_state(runs, "nwp-daily", d3, TODAY, missing_is_failure=True) == "missing"
    g = status.g0(runs, TODAY, missing_is_failure=True)
    assert g["streak_days"] == 2
    assert g["passed"] is False
    # y por defecto (servidor 24/7) status.build lo trata igual
    s = OpsSettings(data_dir=tmp_path)
    s.run_log_path.parent.mkdir(parents=True)
    s.run_log_path.write_text("\n".join(json.dumps(r) for r in runs))
    rep = status.build(s, 4, at(TODAY, 12))
    assert rep["missing_is_failure"] is True
    assert rep["days"][3]["nwp-daily"] == "missing"
    assert rep["g0"]["streak_days"] == 2


def test_laptop_off_day_is_not_a_failure(tmp_path: Path) -> None:
    runs = runs_missing_one_day()
    d3 = TODAY - dt.timedelta(days=3)
    assert status.day_state(runs, "nwp-daily", d3, TODAY, missing_is_failure=False) == "off"
    g = status.g0(runs, TODAY, missing_is_failure=False)
    assert g["streak_days"] == 7  # los 7 días con ejecución; el apagado ni suma ni rompe
    assert g["passed"] is True
    # un fallo real sigue rompiendo aunque el equipo se apague
    runs.append({**run("nwp-daily", TODAY - dt.timedelta(days=5)), "status": "error"})
    assert status.g0(runs, TODAY, missing_is_failure=False)["streak_days"] == 3
    s = OpsSettings(data_dir=tmp_path, missing_is_failure=False)
    s.run_log_path.parent.mkdir(parents=True)
    s.run_log_path.write_text("\n".join(json.dumps(r) for r in runs_missing_one_day()))
    out = status.render(status.build(s, 4, at(TODAY, 12)))
    assert "off, no fallo" in out


# --- exportación y log ---------------------------------------------------------------------
def test_export_contains_ops_and_never_secrets(tmp_path: Path) -> None:
    register_secret(TOKEN)
    s = OpsSettings(data_dir=tmp_path, host_id="vm")
    (tmp_path / ".env").write_text(f"ENTSOE_API_KEY={TOKEN}\n")
    ops = s.ops_path
    (ops / "locks").mkdir(parents=True)
    (ops / "locks" / "x.lock").write_text("")
    (ops / "export_2026-10-01.tar.gz").write_bytes(b"viejo")
    s.run_log_path.write_text(json.dumps({"job": "nwp-daily", "error": f"securityToken={TOKEN}"}))
    s.log_path.write_text("\n".join(f"linea {k} token {TOKEN}" for k in range(50)))
    av.record_availability(s, TODAY, "dynamical", at(TODAY, 8, 18), "F", {}, at(TODAY, 8, 20))
    val = s.validation_path
    val.mkdir(parents=True)
    for name in (
        "ifs_ens_2026-10-10_2026-10-16.json",
        "ifs_ens_2025-01-01_2025-01-07.json",
        "entsoe_prices.json",
    ):
        (val / name).write_text(f'{{"k": "{TOKEN}"}}')
    now = at(TODAY, 12)
    out = export.build_export(s, {"g0": {"passed": False}, "x": TOKEN}, now, days=7, log_lines=10)
    assert out == ops / "export_2026-10-16.tar.gz"
    with tarfile.open(out) as tar:
        names = tar.getnames()
        blob = b"".join(
            f.read() for m in tar.getmembers() if m.isfile() and (f := tar.extractfile(m))
        )
        tail = tar.extractfile("predico-export-2026-10-16/logs/ops.log.tail")
        assert tail is not None
        tail_lines = tail.read().decode().splitlines()
    root = "predico-export-2026-10-16/"
    assert root + "ops/runs.jsonl" in names
    assert any(n.startswith(root + "ops/nwp_availability/_delta_log/") for n in names)
    assert root + "status.json" in names
    assert root + "validation/ifs_ens_2026-10-10_2026-10-16.json" in names
    assert root + "validation/entsoe_prices.json" in names
    assert (
        root + "validation/ifs_ens_2025-01-01_2025-01-07.json" not in names
    )  # fuera de la ventana
    assert not any(".env" in n or "locks" in n or "export_2026-10-01" in n for n in names)
    assert len(tail_lines) == 10
    assert TOKEN.encode() not in blob  # ni en ficheros copiados ni en el log ni en status


def test_tee_to_log_redacts_and_restores_streams(tmp_path: Path) -> None:
    register_secret(TOKEN)
    s = OpsSettings(data_dir=tmp_path)
    real_out = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        with tee_to_log(s, "entsoe-daily"):
            print(f"GET ?securityToken={TOKEN}")
            print("segunda línea")
    finally:
        sys.stdout = real_out
    text = s.log_path.read_text()
    assert TOKEN not in text
    assert "[entsoe-daily] segunda línea" in text
    assert "segunda línea" in buf.getvalue()  # el flujo original sigue recibiendo la salida
    assert jobs  # silencia import no usado
