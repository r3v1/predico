"""Jobs de la operación diaria: `nwp-daily` (F1.5), `entsoe-daily` (F2.7) y `entsoe-hourly` (F2.5).

Envuelven los CLIs/funciones de `ingest` y `features` sin modificarlos. Cada job escribe una
línea en `runs.jsonl` (también si falla) y toma el lock del almacén que escribe.
"""

import contextlib
import datetime as dt
import io
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from predico50hz.features.timing import closure_utc
from predico50hz.orchestration import locks, publication
from predico50hz.orchestration.availability import (
    SOURCE_DYNAMICAL,
    SOURCE_ENFO,
    SnapshotSource,
    poll_until_available,
    record_availability,
)
from predico50hz.orchestration.runlog import Run, summarize_error
from predico50hz.orchestration.settings import OpsSettings


class JobError(RuntimeError):
    pass


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


class _Tee(io.TextIOBase):
    """Reenvía a la salida real y guarda las últimas líneas para resumir el error."""

    def __init__(self, target: Any) -> None:
        self.target, self.lines = target, []  # pyright: ignore[reportUnknownMemberType]

    def write(self, s: str) -> int:
        self.target.write(s)
        self.lines.extend(x for x in s.splitlines() if x.strip())  # pyright: ignore
        del self.lines[:-20]  # pyright: ignore
        return len(s)

    def flush(self) -> None:
        self.target.flush()


def _failure_reason(lines: list[str]) -> str:
    """Motivo del fallo: los `errors` del resumen JSON o las líneas con "error"; si no, el final."""
    reasons: list[str] = []
    for line in lines:
        try:
            info = json.loads(line)
        except ValueError:
            info = None
        if isinstance(info, dict) and info.get("errors"):  # pyright: ignore[reportUnknownMemberType]
            reasons.append(str(info["errors"]))  # pyright: ignore[reportUnknownArgumentType]
        elif info is None and "error" in line.lower():
            reasons.append(line)
    return " | ".join((reasons or lines)[-3:])


def _run_cli(main: Callable[[list[str]], int], argv: list[str], label: str) -> None:
    """Ejecuta un CLI en el mismo proceso; un código distinto de 0 es un fallo con su salida."""
    out, err = _Tee(sys.stdout), _Tee(sys.stderr)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    if rc != 0:
        reason = _failure_reason([*err.lines, *out.lines])  # pyright: ignore
        raise JobError(summarize_error(f"{label} terminó con código {rc}: {reason}"))


# -- nwp-daily -------------------------------------------------------------------------------


def _is_ingested(settings: OpsSettings, day: dt.date) -> bool:
    from predico50hz.ingest.dynamical.bronze import BronzeStore  # noqa: PLC0415

    return BronzeStore(settings.nwp_bronze).is_ingested(day)


def plan_catchup(today: dt.date, n_days: int, ingested: Callable[[dt.date], bool]) -> list[dt.date]:
    """Días de los `n_days` anteriores a `today` sin ingerir, del más antiguo al más nuevo."""
    days = (today - dt.timedelta(days=k) for k in range(n_days, 0, -1))
    return [d for d in days if not ingested(d)]


def _ingest_day(settings: OpsSettings, day: dt.date, snapshot_id: str) -> dict[str, Any]:
    """Bronce + plata de una pasada leyendo el snapshot indicado, bajo el lock del bronce."""
    from predico50hz.ingest.dynamical.bronze import BronzeStore  # noqa: PLC0415
    from predico50hz.ingest.dynamical.pipeline import Config, run_pass  # noqa: PLC0415
    from predico50hz.ingest.dynamical.source import open_source  # noqa: PLC0415
    from predico50hz.ingest.ifs.extract import load_points  # noqa: PLC0415

    with locks.file_lock(settings.lock_path, locks.NWP_BRONZE, settings.lock_timeout_s):
        src = open_source(snapshot_id=snapshot_id)
        cfg = Config(
            BronzeStore(settings.nwp_bronze),
            settings.nwp_silver_path,
            load_points(str(settings.centroids)),
        )
        m = run_pass(src, cfg, day, skip_existing=True)
    if m.status not in ("ok", "skipped"):
        raise JobError(f"ingesta de {day} con estado {m.status}")
    return {
        "day": day.isoformat(),
        "status": m.status,
        "read_s": round(m.read_s, 1),
        "mb": round(m.mb_transferred),
        "nan_fraction": m.n_nan_fraction,
        "bronze_commit": m.bronze_commit,
        "source_snapshot_id": src.snapshot_id,
    }


def _validate(settings: OpsSettings, end: dt.date, must_pass: set[dt.date]) -> dict[str, Any]:
    """F1.6 sobre los últimos `validate_days`; solo falla por los días que acabamos de ingerir.

    Los días anteriores sin ingerir (VM nueva, portátil apagado) salen como huecos en el informe
    pero no cuentan como fallo.
    """
    from predico50hz.ingest.dynamical import validate as dyn_validate  # noqa: PLC0415

    start = end - dt.timedelta(days=max(1, settings.validate_days) - 1)
    rep = dyn_validate.run(
        start, end, settings.nwp_bronze, settings.nwp_silver_path, centroids=settings.centroids
    )
    out = settings.validation_path / f"ifs_ens_{start}_{end}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    bad = sorted(
        g["init_date"] for g in rep["gaps"] if dt.date.fromisoformat(g["init_date"]) in must_pass
    )
    if bad or rep["bronze_structure_problems"]:
        raise JobError(
            f"validación F1.6 no superada: días {bad}, "
            f"estructura {rep['bronze_structure_problems']}"
        )
    return {
        "coverage": rep["coverage"],
        "gaps_tolerated": [g["init_date"] for g in rep["gaps"]],
        "report": str(out),
    }


def _gold(settings: OpsSettings, start: dt.date, end: dt.date) -> None:
    from predico50hz.features.__main__ import main as features_main  # noqa: PLC0415

    with locks.file_lock(settings.lock_path, locks.GOLD, settings.lock_timeout_s):
        _run_cli(
            features_main,
            [
                "--start",
                start.isoformat(),
                "--end",
                end.isoformat(),
                "--gold",
                str(settings.gold_path),
                "--validation",
                str(settings.validation_path),
                "--nwp",
                str(settings.nwp_silver_path),
                "--generation",
                str(settings.entsoe_silver_path / "generation"),
                "--cap-cluster",
                str(settings.mastr_silver_path / "capacity_cluster"),
                "--cap-total",
                str(settings.mastr_silver_path / "capacity_total"),
                "--centroids",
                str(settings.centroids),
            ],
            "features",
        )


def nwp_daily(  # noqa: PLR0915
    settings: OpsSettings,
    day: dt.date | None = None,
    source: SnapshotSource | None = None,
) -> dict[str, Any]:
    """Pasada de hoy + puesta al día de los `catchup_days` anteriores.

    Hoy: se espera hasta `poll_deadline_utc` (solo la espera); si ya pasó, un único sondeo. Si hoy
    no existe, el job falla, pero antes se ingieren las pasadas anteriores que sí estén. Las
    pasadas recuperadas llevan `catch_up=true`: su `available_at` (commit del snapshot) es real,
    su `detected_at` es tardío y no sirve para el margen operativo.
    """
    from predico50hz.orchestration.availability import (  # noqa: PLC0415
        CachedSnapshots,
        IcechunkDynamical,
        find_complete_snapshot,
    )

    day = day or _today()
    src = source or IcechunkDynamical()
    deadline = dt.datetime.combine(day, settings.poll_deadline_utc, tzinfo=dt.UTC)
    ingested_days: set[dt.date] = set()
    errors: list[str] = []
    margin: float | None = None
    with Run(settings, "nwp-daily", day) as run:
        run.details["init_day"] = day.isoformat()
        pending = plan_catchup(day, settings.catchup_days, lambda d: _is_ingested(settings, d))
        run.details["catchup_pending"] = [d.isoformat() for d in pending]

        with run.phase("poll") as ph:
            poll = poll_until_available(settings, day, src)
            ph.detail |= {"polls": poll.polls, "notes": poll.notes}
            if poll.enfo_available_at is not None and poll.enfo_detected_at is not None:
                added = record_availability(
                    settings,
                    day,
                    SOURCE_ENFO,
                    poll.enfo_available_at,
                    f"enfo-ef.index {settings.enfo_step_h}h",
                    {"step_h": settings.enfo_step_h, "measure": "Last-Modified del .index"},
                    poll.enfo_detected_at,
                    catch_up=poll.enfo_detected_at > deadline,
                )
                run.details["enfo"] = {
                    "available_at": poll.enfo_available_at.isoformat(timespec="seconds"),
                    "new_row": added,
                }
            det = poll.detection
            if det is not None and poll.detected_at is not None:
                late = poll.detected_at > deadline
                margin = (closure_utc(day) - det.available_at).total_seconds() / 60
                added = record_availability(
                    settings,
                    day,
                    SOURCE_DYNAMICAL,
                    det.available_at,
                    det.snapshot_id,
                    {"message": det.message, "polls": poll.polls},
                    poll.detected_at,
                    catch_up=late,
                )
                ph.detail |= {
                    "snapshot_id": det.snapshot_id,
                    "available_at": det.available_at.isoformat(timespec="seconds"),
                    "detected_at": poll.detected_at.isoformat(timespec="seconds"),
                    "margin_to_closure_min": round(margin, 1),
                    "catch_up": late,
                    "new_row": added,
                }
                run.details["dynamical"] = dict(ph.detail)
            else:
                ph.detail["available"] = False  # no es fatal aquí: se decide al final

        if det is not None:
            with run.phase("ingest") as ph:
                ph.detail |= _ingest_day(settings, day, det.snapshot_id)
            ingested_days.add(day)

        if pending:
            with run.phase("catchup") as ph:
                cached = CachedSnapshots(
                    src, dt.datetime.combine(pending[0], dt.time(0), tzinfo=dt.UTC)
                )
                recovered: list[str] = []
                unavailable: list[str] = []
                for p in pending:
                    found = find_complete_snapshot(cached, p)
                    if found is None:
                        unavailable.append(p.isoformat())
                        continue
                    try:
                        record_availability(
                            settings,
                            p,
                            SOURCE_DYNAMICAL,
                            found.available_at,
                            found.snapshot_id,
                            {"message": found.message},
                            dt.datetime.now(dt.UTC),
                            catch_up=True,
                        )
                        _ingest_day(settings, p, found.snapshot_id)
                        ingested_days.add(p)
                        recovered.append(p.isoformat())
                    except Exception as e:  # una pasada mala no impide las demás
                        errors.append(f"{p}: {summarize_error(e)}")
                ph.detail |= {"recovered": recovered, "unavailable": unavailable, "errors": errors}

        if ingested_days:
            with run.phase("validate") as ph:
                ph.detail |= _validate(settings, day, ingested_days)
            with run.phase("gold"):  # variables (el objetivo de hoy sale nulo)
                _gold(settings, min(ingested_days), max(ingested_days))

        run.details["margin_to_closure_min"] = None if margin is None else round(margin, 1)
        if det is None:
            raise JobError(
                f"la pasada 00Z de {day} no está completa en dynamical a las "
                f"{settings.poll_deadline_utc:%H:%M} UTC (sondeos: {poll.polls}); "
                "ver docs/runbooks/nwp-no-llega.md"
            )
        if errors:
            raise JobError("; ".join(errors))
    return {"day": day.isoformat(), "margin_min": margin, "caught_up": len(ingested_days) - 1}


# -- entsoe ----------------------------------------------------------------------------------


def _entsoe_argv(settings: OpsSettings, dataset: str, days: int) -> list[str]:
    return [
        "--dataset",
        dataset,
        "--days",
        str(days),
        "--bronze",
        str(settings.entsoe_bronze_path),
        "--silver",
        str(settings.entsoe_silver_path),
        "--validation",
        str(settings.validation_path),
    ]


def _entsoe(  # noqa: PLR0913, PLR0917
    settings: OpsSettings,
    job: str,
    datasets: list[str],
    days: int,
    vacuum: bool = False,
    after: Callable[[Run], None] | None = None,
) -> None:
    from predico50hz.ingest.entsoe.__main__ import main as entsoe_main  # noqa: PLC0415

    errors: list[str] = []
    with Run(settings, job, _today()) as run:
        for ds in datasets:
            try:
                with (
                    run.phase(ds) as ph,
                    locks.file_lock(settings.lock_path, locks.ENTSOE, settings.lock_timeout_s),
                ):
                    t = time.perf_counter()
                    _run_cli(entsoe_main, _entsoe_argv(settings, ds, days), f"entsoe {ds}")
                    ph.detail["days"] = days
                    ph.detail["wall_s"] = round(time.perf_counter() - t, 1)
            except Exception as e:  # una fase fallida no impide intentar las demás
                errors.append(f"{ds}: {summarize_error(e)}")
        if vacuum:
            try:
                with (
                    run.phase("vacuum") as ph,
                    locks.file_lock(settings.lock_path, locks.ENTSOE, settings.lock_timeout_s),
                ):
                    ph.detail["files_removed"] = vacuum_silver(settings)
            except Exception as e:  # mantenimiento: se anota la fase, pero no es un fallo del job
                run.details["vacuum_error"] = summarize_error(e)
        if after is not None:
            after(run)
        if errors:
            raise JobError("; ".join(errors))


def vacuum_silver(settings: OpsSettings) -> dict[str, int]:
    """Borra los ficheros de versiones antiguas de la plata de ENTSO-E (Delta); devuelve cuántos.

    `prices` y `forecast` guardan `fetched_at` y se reescriben enteros en cada descarga horaria
    (~10 MB por versión en `forecast`); sin purga crecerían ~250 MB al día. La plata siempre se
    puede reconstruir desde el bronce, así que no se necesita viaje en el tiempo.
    """
    from deltalake import DeltaTable  # noqa: PLC0415

    out: dict[str, int] = {}
    for table in sorted(settings.entsoe_silver_path.glob("*")):
        if not (table / "_delta_log").exists():
            continue
        removed = DeltaTable(str(table)).vacuum(
            retention_hours=settings.vacuum_retention_h,
            enforce_retention_duration=False,
            dry_run=False,
        )
        out[table.name] = len(removed)
    return out


def entsoe_daily(settings: OpsSettings, days: int = 3) -> None:
    _entsoe(settings, "entsoe-daily", ["all"], days, vacuum=True)


def _probe_generation(
    settings: OpsSettings, now: dt.datetime, client_factory: Callable[[], Any] | None = None
) -> dict[str, Any]:
    """Consulta ligera del último paso de generación real publicado y lo anota."""
    from predico50hz.ingest.entsoe.client import EntsoeClient  # noqa: PLC0415
    from predico50hz.settings import get_settings  # noqa: PLC0415

    if client_factory is None:
        key = get_settings().entsoe_api_key
        if key is None:
            raise JobError("falta ENTSOE_API_KEY en .env")
        client_factory = lambda: EntsoeClient(key.get_secret_value())  # noqa: E731
    client = client_factory()
    try:
        obs = publication.probe_generation(client, now)
    finally:
        client.close()
    publication.append_observation(settings.generation_lag_path, obs)
    return obs


def entsoe_hourly(
    settings: OpsSettings,
    days: int = 2,
    now: dt.datetime | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> None:
    def probe(run: Run) -> None:
        # la medida de generación no hace fallar el job: el fallo queda en los detalles
        try:
            with locks.file_lock(settings.lock_path, locks.ENTSOE, settings.lock_timeout_s):
                obs = _probe_generation(settings, now or dt.datetime.now(dt.UTC), client_factory)
            run.details["generation_last_step"] = obs["last_step"]
        except Exception as e:
            run.details["generation_error"] = summarize_error(e)

    _entsoe(settings, "entsoe-hourly", ["prices", "forecast"], days, after=probe)


# -- publication-times -----------------------------------------------------------------------


def publication_times(
    settings: OpsSettings, since: dt.date | None = None, now: dt.datetime | None = None
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    out = settings.publication_path
    previous = json.loads(out.read_text()) if out.exists() else None
    since = since or now.date() - dt.timedelta(days=60)
    with Run(settings, "publication-times", now.date()) as run:
        with run.phase("compute"):
            res = publication.compute(
                settings.entsoe_bronze_path, since, now, previous, settings.generation_lag_path
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(out) + ".tmp")
        tmp.write_text(json.dumps(res, indent=2, ensure_ascii=False))
        tmp.replace(out)
        run.details["summary"] = {k: v["summary"] for k, v in res["datasets"].items()}
    return res
