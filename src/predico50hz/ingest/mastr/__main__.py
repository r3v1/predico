"""CLI: `uv run python -m predico50hz.ingest.mastr --download` (o `--export-date AAAAMMDD`).

Descarga (opcional) el Gesamtdatenexport al NAS, extrae las unidades eólicas de la zona de
50Hertz, calcula clústeres y capacidad diaria, escribe la plata en `data/silver/mastr/`,
`config/centroids.yaml` y el informe `data/validation/mastr.json`.
"""

import argparse
import sys
from pathlib import Path

from predico50hz.ingest.mastr.config import (
    DEFAULT_CENTROIDS,
    DEFAULT_SILVER,
    DEFAULT_VALIDATION,
    RAW_ROOT,
)
from predico50hz.ingest.mastr.download import download_export, latest_export_url, raw_dir
from predico50hz.ingest.mastr.pipeline import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download", action="store_true", help="baja el export más reciente al NAS")
    ap.add_argument(
        "--export-date", help="AAAAMMDD del export ya descargado (por defecto, el último)"
    )
    ap.add_argument("--raw-root", default=RAW_ROOT)
    ap.add_argument("--k", type=int, help="clústeres terrestres (por defecto, criterio automático)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--silver", default=DEFAULT_SILVER)
    ap.add_argument("--validation", default=DEFAULT_VALIDATION)
    ap.add_argument("--centroids", default=DEFAULT_CENTROIDS)
    args = ap.parse_args(argv)

    export_date = args.export_date
    if args.download:
        url, export_date = latest_export_url()
        dest = raw_dir(export_date, args.raw_root) / Path(url).name
        print(f"descargando {url} -> {dest}", flush=True)
        download_export(url, dest)
    if export_date is None:
        dates = sorted(p.name for p in Path(args.raw_root).iterdir() if p.is_dir())
        if not dates:
            print(f"no hay exports en {args.raw_root}; usa --download", file=sys.stderr)
            return 2
        export_date = dates[-1]
    zips = sorted(raw_dir(export_date, args.raw_root).glob("Gesamtdatenexport_*.zip"))
    if not zips:
        print(f"falta el ZIP del export {export_date} en {args.raw_root}", file=sys.stderr)
        return 2
    rep = run(
        zips[-1],
        export_date,
        Path(args.silver),
        Path(args.validation),
        Path(args.centroids),
        args.k,
        args.seed,
    )
    print(
        f"export {export_date}: {rep['units_in_zone']} unidades en zona, "
        f"k_land={rep['clusters']['k_land']}, schema_ok={rep['schema_ok']}",
        flush=True,
    )
    return 0 if rep["schema_ok"] and not rep["clusters"]["centroids_outside_domain"] else 1


if __name__ == "__main__":
    sys.exit(main())
