"""CLI único: `uv run python -m predico50hz.orchestration <job>`.

Jobs: `nwp-daily`, `entsoe-daily`, `entsoe-hourly`, `status`, `publication-times`, más `serve`
(planificador para Docker) y `record-failure` (lo usa la unidad systemd `OnFailure=`)
y `wait-online` (`ExecStartPre=`: espera a que haya red).
Código de salida 0 si el job fue bien y 1 si falló (el motivo queda en `runs.jsonl`).
"""

import argparse
import datetime as dt
import json
import sys
from contextlib import nullcontext
from pathlib import Path

from predico50hz.ingest.entsoe.redact import install_log_redaction, redact
from predico50hz.orchestration import export, jobs, network, publication, scheduler, status
from predico50hz.orchestration.runlog import record_unit_failure, tee_to_log
from predico50hz.orchestration.settings import OpsSettings, get_ops_settings

QUIET = frozenset({"status", "export", "serve", "wait-online"})  # no escriben en ops.log


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _time(s: str) -> dt.time:
    return dt.time.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="predico50hz.orchestration", description=__doc__)
    sub = ap.add_subparsers(dest="job", required=True)
    p = sub.add_parser("nwp-daily", help="pasada 00Z: espera, registra hora, ingesta, plata, oro")
    p.add_argument("--day", type=_date, help="día de la pasada (por defecto, hoy UTC)")
    p.add_argument("--poll-interval-s", type=float)
    p.add_argument("--deadline-utc", type=_time, help="HH:MM UTC límite de espera")
    p.add_argument("--validate-days", type=int, help="días de la validación F1.6 (defecto 7)")
    p.add_argument("--catchup-days", type=int, help="pasadas anteriores a recuperar (defecto 14)")
    p = sub.add_parser("entsoe-daily", help="--dataset all --days 3")
    p.add_argument("--days", type=int, default=3)
    p = sub.add_parser("entsoe-hourly", help="precios y pronóstico del operador, --days 2")
    p.add_argument("--days", type=int, default=2)
    p = sub.add_parser("status", help="resumen de los últimos días y puerta G0")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("publication-times", help="hora de primera publicación de ENTSO-E")
    p.add_argument("--since", type=_date, help="primer cierre a medir (por defecto, hace 60 días)")
    p = sub.add_parser("export", help="data/ops/export_<fecha>.tar.gz para revisar G0")
    p.add_argument("--days", type=int, default=7, help="días de informes de validación")
    p.add_argument("--log-lines", type=int, default=2000)
    p.add_argument("--out", type=Path)
    sub.add_parser("serve", help="planificador (Docker o cualquier servidor)")
    p = sub.add_parser("wait-online", help="espera a que los hosts del job sean alcanzables")
    p.add_argument("--for", dest="target", choices=sorted(network.HOSTS), required=True)
    p.add_argument("--timeout-s", type=float, default=600.0)
    p = sub.add_parser("record-failure", help="registra el fallo de una unidad systemd")
    p.add_argument("--unit", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    install_log_redaction()
    settings = get_ops_settings()
    try:
        with tee_to_log(settings, args.job) if args.job not in QUIET else nullcontext():
            _dispatch(args, settings)
    except Exception as e:  # el detalle ya está en runs.jsonl; aquí solo el resumen, redactado
        print(f"error: {type(e).__name__}: {redact(str(e))}", file=sys.stderr)
        return 1
    return 0


def _dispatch(args: argparse.Namespace, settings: OpsSettings) -> None:  # noqa: PLR0912
    match args.job:
        case "nwp-daily":
            if args.poll_interval_s:
                settings.poll_interval_s = args.poll_interval_s
            if args.deadline_utc:
                settings.poll_deadline_utc = args.deadline_utc
            if args.validate_days:
                settings.validate_days = args.validate_days
            if args.catchup_days is not None:
                settings.catchup_days = args.catchup_days
            r = jobs.nwp_daily(settings, args.day)
            margin = "?" if r["margin_min"] is None else f"{r['margin_min']:.0f}"
            print(f"nwp-daily {r['day']}: ok, margen al cierre {margin} min")
        case "entsoe-daily":
            jobs.entsoe_daily(settings, args.days)
        case "entsoe-hourly":
            jobs.entsoe_hourly(settings, args.days)
        case "publication-times":
            res = jobs.publication_times(settings, args.since)
            print(publication.render(res))
            print(f"\nescrito {settings.publication_path}")
        case "status":
            rep = status.build(settings, args.days, dt.datetime.now(dt.UTC))
            print(
                json.dumps(rep, indent=2, ensure_ascii=False) if args.json else status.render(rep)
            )
        case "export":
            now = dt.datetime.now(dt.UTC)
            jobs.publication_times(settings)
            rep = status.build(settings, args.days, now)
            print(export.build_export(settings, rep, now, args.days, args.log_lines, args.out))
        case "serve":
            scheduler.serve(settings)
        case "wait-online":
            if not network.wait_online(network.HOSTS[args.target], args.timeout_s):
                raise RuntimeError(f"sin red hacia {network.HOSTS[args.target]}")
        case "record-failure":
            record_unit_failure(settings, args.unit)
        case _:
            raise AssertionError(args.job)


if __name__ == "__main__":
    sys.exit(main())
