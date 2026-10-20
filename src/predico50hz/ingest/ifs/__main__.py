"""CLI: `uv run python -m predico50hz.ingest.ifs --start 2026-10-01 --end 2026-10-07 --cycle 00`."""

import argparse
import datetime as dt
import json
from pathlib import Path

from predico50hz.ingest.ifs.bronze import BronzeRepo
from predico50hz.ingest.ifs.download import Downloader
from predico50hz.ingest.ifs.extract import load_points
from predico50hz.ingest.ifs.pipeline import Config, run_pass

DEFAULT_BRONZE = "/mnt/data/nwp/IFS/icechunk"


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_date, required=True)
    ap.add_argument("--end", type=_date, required=True)
    ap.add_argument("--cycle", type=int, nargs="+", default=[0], choices=[0, 6, 12, 18])
    ap.add_argument("--steps", type=int, nargs="+", help="por defecto 18..54 (00Z) o 12..48 (06Z)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--bronze", default=DEFAULT_BRONZE)
    ap.add_argument("--silver", default="data/silver/ifs_points")
    ap.add_argument("--centroids", default="config/centroids.yaml")
    ap.add_argument("--metrics", default="data/stage1/metrics.json")
    ap.add_argument("--label", default="run")
    args = ap.parse_args(argv)

    cfg = Config(
        Path(args.bronze), Path(args.silver), load_points(args.centroids), args.concurrency
    )
    repo = BronzeRepo(cfg.bronze)
    dl = Downloader(args.concurrency)
    passes: list[dict[str, object]] = []
    day = args.start
    while day <= args.end:
        for cyc in args.cycle:
            m = run_pass(dl, repo, cfg, day, cyc, tuple(args.steps) if args.steps else None)
            passes.append(m.as_dict())
            print(
                f"{m.date} {cyc:02d}Z {m.res} {m.status} {m.mb_transferred:.1f} MB "
                f"dl={m.download_s:.1f}s ({m.throughput_mb_s:.1f} MB/s) 503={m.n503} "
                f"dec={m.decode_crop_s:.1f}s bronze={m.bronze_write_s:.1f}s "
                f"silver={m.silver_s:.1f}s",
                flush=True,
            )
        day += dt.timedelta(days=1)

    out = Path(args.metrics)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, list[dict[str, object]]] = (
        json.loads(out.read_text()) if out.exists() else {"runs": []}
    )
    doc["runs"].append(
        {
            "label": args.label,
            "bronze": str(cfg.bronze),
            "concurrency": args.concurrency,
            "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "passes": passes,
        }
    )
    out.write_text(json.dumps(doc, indent=1))


if __name__ == "__main__":
    main()
