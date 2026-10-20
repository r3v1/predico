"""CLI: `uv run python -m predico50hz.features [--start AAAA-MM-DD --end AAAA-MM-DD]`.

Construye la tabla oro `data/gold/features` (Delta) desde la plata, la valida con Pandera y
escribe `data/validation/gold_features.json`. `--start/--end` son fechas de cierre y solo
reemplazan esas fechas; sin ellas se reconstruye todo. Código de salida 1 si hay problemas.
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from predico50hz.features.build import Sources, build_gold, write_gold
from predico50hz.features.capacity import load_clusters
from predico50hz.features.config import (
    DEFAULT_CAP_CLUSTER,
    DEFAULT_CAP_TOTAL,
    DEFAULT_CENTROIDS,
    DEFAULT_GENERATION,
    DEFAULT_GOLD,
    DEFAULT_NWP,
    DEFAULT_VALIDATION,
)
from predico50hz.features.validate import validate


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_date, help="primera fecha de cierre (por defecto, todas)")
    ap.add_argument("--end", type=_date, help="última fecha de cierre (por defecto, todas)")
    ap.add_argument("--validate-only", action="store_true", help="no construye, solo valida")
    ap.add_argument("--gold", default=DEFAULT_GOLD)
    ap.add_argument("--validation", default=DEFAULT_VALIDATION)
    ap.add_argument("--nwp", default=DEFAULT_NWP)
    ap.add_argument("--generation", default=DEFAULT_GENERATION)
    ap.add_argument("--cap-cluster", default=DEFAULT_CAP_CLUSTER)
    ap.add_argument("--cap-total", default=DEFAULT_CAP_TOTAL)
    ap.add_argument("--centroids", default=DEFAULT_CENTROIDS)
    args = ap.parse_args(argv)

    gold = Path(args.gold)
    if not args.validate_only:
        src = Sources(args.nwp, args.generation, args.cap_cluster, args.cap_total, args.centroids)
        t0 = time.perf_counter()
        df, info = build_gold(src, args.start, args.end)
        if df.is_empty():
            print("no hay pasadas NWP en el rango pedido", file=sys.stderr)
            return 1
        written = write_gold(df, gold, args.start, args.end)
        info |= {
            "rows": df.height,
            "written": written,
            "total_seconds": round(time.perf_counter() - t0, 2),
            "gold_bytes": _du(gold),
        }
        print(json.dumps(info), flush=True)
    rep = validate(gold, Path(args.validation), load_clusters(args.centroids))
    print(
        f"gold: schema_ok={rep['schema_ok']} filas={rep['rows']} días={rep['days']} "
        f"problemas={len(rep['problems'])}",
        flush=True,
    )
    return 0 if rep["schema_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
