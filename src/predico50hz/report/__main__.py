# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""CLI: `uv run python -m predico50hz.report --out report/index.html`."""

import argparse
from pathlib import Path

from predico50hz.report.build import build
from predico50hz.report.paths import Paths


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="report/index.html", help="HTML de salida")
    ap.add_argument("--root", default=".", help="raíz del proyecto (data/, config/, report/)")
    ap.add_argument("--no-json", action="store_true", help="no escribe data.json junto al HTML")
    ap.add_argument("--offline", action="store_true", help="no consulta GitHub; usa la caché")
    args = ap.parse_args(argv)
    out = build(
        Paths(root=Path(args.root)),
        Path(args.out),
        dump_json=not args.no_json,
        offline=args.offline,
    )
    print(f"{out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
