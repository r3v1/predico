"""CLI: `uv run python -m predico50hz.ingest.dynamical --start 2026-10-01 --end 2026-10-07`."""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from predico50hz.ingest.dynamical.bronze import BronzeStore
from predico50hz.ingest.dynamical.config import (
    DEFAULT_BRONZE,
    DEFAULT_CENTROIDS,
    DEFAULT_METRICS,
    DEFAULT_SILVER,
    DOMAIN,
    VARIABLES,
)
from predico50hz.ingest.dynamical.pipeline import Config, net_rx_bytes, read_stage, write_stage
from predico50hz.ingest.dynamical.source import open_source, read_cube
from predico50hz.ingest.ifs.extract import load_points
from predico50hz.orchestration import locks
from predico50hz.orchestration.settings import get_ops_settings


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _bench(
    src_href: str | None, snapshot: str | None, day: dt.date, levels: list[int]
) -> list[dict[str, float]]:
    """Solo lectura de una pasada con distintas concurrencias de variables (sin escribir)."""
    src = open_source(src_href, snapshot)
    init = np.datetime64(day.isoformat(), "ns")
    out: list[dict[str, float]] = []
    for w in levels:
        rx0 = net_rx_bytes()
        t = time.perf_counter()
        read_cube(src, init, VARIABLES, DOMAIN, w)
        dtm = time.perf_counter() - t
        out.append({"workers": w, "read_s": dtm, "mb": (net_rx_bytes() - rx0) / 1e6})
        print(f"workers={w}: {dtm:.1f} s, {out[-1]['mb']:.0f} MB", flush=True)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_date, required=True)
    ap.add_argument("--end", type=_date, required=True)
    ap.add_argument("--workers", type=int, default=5, help="variables leidas en paralelo")
    ap.add_argument("--init-workers", type=int, default=1, help="pasadas leidas a la vez")
    ap.add_argument("--bronze", default=DEFAULT_BRONZE)
    ap.add_argument("--silver", default=DEFAULT_SILVER)
    ap.add_argument("--centroids", default=DEFAULT_CENTROIDS)
    ap.add_argument("--metrics", default=DEFAULT_METRICS)
    ap.add_argument("--href", help="href del Icechunk de origen (por defecto, el del STAC)")
    ap.add_argument("--snapshot-id", help="fija el snapshot de origen (reproducibilidad)")
    ap.add_argument("--skip-existing", action="store_true", help="omite pasadas ya en bronce")
    ap.add_argument(
        "--lock-dir",
        help="directorio de locks (disco local); por defecto, el de los ajustes PREDICO_*",
    )
    ap.add_argument(
        "--bench", type=int, nargs="+", help="mide la lectura de --start con estas concurrencias"
    )
    ap.add_argument("--label", default="run")
    args = ap.parse_args(argv)

    out = Path(args.metrics)
    doc: dict[str, list[dict[str, object]]] = (
        json.loads(out.read_text()) if out.exists() else {"runs": []}
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.bench:
        res = _bench(args.href, args.snapshot_id, args.start, args.bench)
        doc.setdefault("bench", []).append(
            {"label": args.label, "date": args.start.isoformat(), "results": res}
        )  # pyright: ignore[reportArgumentType]
        out.write_text(json.dumps(doc, indent=1))
        return

    settings = get_ops_settings()
    lock_dir = Path(args.lock_dir) if args.lock_dir else settings.lock_path
    src = open_source(args.href, args.snapshot_id)
    cfg = Config(
        BronzeStore(args.bronze), Path(args.silver), load_points(args.centroids), args.workers
    )
    passes: list[dict[str, object]] = []
    t0 = time.perf_counter()
    days = [args.start + dt.timedelta(days=k) for k in range((args.end - args.start).days + 1)]
    todo = [d for d in days if not (args.skip_existing and cfg.bronze.is_ingested(d))]
    # lecturas en hilos (hasta --init-workers pasadas a la vez); escritura secuencial y en orden
    with ThreadPoolExecutor(max_workers=max(1, args.init_workers)) as ex:
        futures = [ex.submit(read_stage, src, cfg, d) for d in todo]
        for d, fut in zip(todo, futures, strict=True):
            rr = fut.result()  # la descarga va sin lock, para no bloquear a nwp-daily
            # la escritura (bronce + plata) toma el mismo lock que nwp-daily; si no se libera
            # a tiempo, LockTimeoutError aborta el relleno
            with locks.file_lock(lock_dir, locks.NWP_BRONZE, settings.lock_timeout_s):
                m = write_stage(rr, src, cfg, d)
            passes.append(m.as_dict())
            print(
                f"{m.init_date} {m.status} read={m.read_s:.1f}s ({m.mb_transferred:.0f} MB) "
                f"bronze={m.bronze_s:.1f}s silver={m.silver_s:.2f}s",
                flush=True,
            )
    doc["runs"].append(  # pyright: ignore[reportArgumentType]
        {
            "label": args.label,
            "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "wall_s": time.perf_counter() - t0,
            "source_href": src.href,
            "source_snapshot_id": src.snapshot_id,
            "workers": args.workers,
            "init_workers": args.init_workers,
            "passes": passes,
            "bronze_bytes": cfg.bronze.disk_bytes(),
        }
    )
    out.write_text(json.dumps(doc, indent=1))


if __name__ == "__main__":
    main()
