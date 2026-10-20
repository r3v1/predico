"""CLI: `uv run python -m predico50hz.ingest.entsoe --dataset generation --start 2015-01-01`.

Descarga a bronce (una versión `fetched_at=<UTC>` por ejecución), reconstruye la plata desde el
bronce y escribe `data/validation/entsoe_<dataset>.json`. Todo en UTC.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from predico50hz.ingest.entsoe.client import EntsoeClient
from predico50hz.ingest.entsoe.datasets import (
    DATASETS,
    DEFAULT_BRONZE,
    DEFAULT_SILVER,
    DEFAULT_VALIDATION,
)
from predico50hz.ingest.entsoe.pipeline import default_end, fetch_dataset
from predico50hz.ingest.entsoe.redact import install_log_redaction, redact
from predico50hz.ingest.entsoe.silver import build_silver
from predico50hz.ingest.entsoe.validate import validate
from predico50hz.settings import get_settings

# La capacidad va antes que la generación: la validación de generación la usa.
ORDER = ("capacity", "generation", "prices", "forecast")


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=[*DATASETS, "all"], required=True)
    ap.add_argument("--start", type=_date, help="por defecto, el inicio de la historia del dataset")
    ap.add_argument("--end", type=_date, help="por defecto, hoy + 2 días (o fin de año)")
    ap.add_argument("--days", type=int, help="descarga solo los últimos N días (modo diario)")
    ap.add_argument(
        "--resume", metavar="FETCHED_AT", help="reanuda esa descarga (AAAAMMDDTHHMMSSZ)"
    )
    ap.add_argument("--no-fetch", action="store_true", help="solo reconstruye plata e informe")
    ap.add_argument("--bronze", default=DEFAULT_BRONZE)
    ap.add_argument("--silver", default=DEFAULT_SILVER)
    ap.add_argument("--validation", default=DEFAULT_VALIDATION)
    args = ap.parse_args(argv)

    install_log_redaction()
    names = [d for d in ORDER if d in DATASETS] if args.dataset == "all" else [args.dataset]
    bronze, silver, out = Path(args.bronze), Path(args.silver), Path(args.validation)
    client: EntsoeClient | None = None
    if not args.no_fetch:
        key = get_settings().entsoe_api_key
        if key is None:
            print("falta ENTSOE_API_KEY en .env", file=sys.stderr)
            return 2
        client = EntsoeClient(key.get_secret_value())
    today = dt.datetime.now(dt.UTC).date()
    rc = 0
    try:
        for name in names:
            ds = DATASETS[name]
            if client:
                start = args.start or (
                    today - dt.timedelta(days=args.days) if args.days else ds.start
                )
                info = fetch_dataset(
                    client, ds, start, args.end or default_end(ds, today), bronze, args.resume
                )
                print(json.dumps(info, ensure_ascii=False, default=str), flush=True)
                rc = rc or int(bool(info["errors"]))
            print(json.dumps(build_silver(name, bronze, silver), default=str), flush=True)
            rep = validate(name, bronze, silver, out)
            print(f"{name}: schema_ok={rep['schema_ok']} filas={rep['rows']}", flush=True)
    except Exception as e:  # mensaje redactado, sin traza con la URL
        print(f"error: {type(e).__name__}: {redact(str(e))}", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
