"""F3.4: compara interpolación lineal, PCHIP y spline cúbico sobre pasadas NWP reales.

Quita los pasos impares de la malla de 3 h (queda una malla de 6 h: +18, +24, ..., +54 h) y
reconstruye los 6 pasos intermedios de cada pasada. Error = reconstruido - real, agrupado sobre
todas las pasadas, clústeres y pasos. Escribe `data/validation/gold_interp_check.json`.

La prueba 6 h -> 3 h EXAGERA el error de 3 h -> 15 min (el paso es 2 veces mayor que el real
y la curvatura que debe seguir, la de 6 h, es mucho mayor que la de 3 h).

Uso: uv run python scripts/f3_interp_check.py [--nwp data/silver/ifs_ens_clusters]
"""

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import polars as pl

from predico50hz.features import interpolate as itp
from predico50hz.features.config import DEFAULT_NWP, DEFAULT_VALIDATION, STATS
from predico50hz.features.nwp import INSTANT_VARS, LEADS, list_init_dates

METHODS = ("linear", "pchip", "cubic")
MAIN = (
    "wind_speed_100m_mean",
    "wind_speed_100m_ctrl",
    "temperature_2m_mean",
    "pressure_surface_mean",
)
THRESHOLD = 0.05  # PCHIP debe mejorar el RMSE de wind_speed_100m_mean en más de un 5 %


def load(root: Path) -> tuple[np.ndarray, list[str], int]:
    names = [f"{v}_{s}" for v in INSTANT_VARS for s in STATS]
    cubes = []
    for d in list_init_dates(root):
        df = pl.read_parquet(root / f"init_date={d.isoformat()}" / "part-0.parquet")
        df = df.sort("lead_hours", "point_id")
        n_p = df.get_column("point_id").n_unique()
        cubes.append(np.stack([df[n].to_numpy().reshape(len(LEADS), n_p) for n in names], -1))
    return np.stack(cubes).astype(np.float64), names, len(cubes)  # (N, 13, P, C)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nwp", default=DEFAULT_NWP)
    ap.add_argument("--out", default=f"{DEFAULT_VALIDATION}/gold_interp_check.json")
    args = ap.parse_args()
    data, names, n_pass = load(Path(args.nwp))
    t = np.array(LEADS, dtype=np.float64) * 3600.0
    coarse = np.arange(0, len(LEADS), 2)
    fine = np.arange(1, len(LEADS), 2)
    truth = data[:, fine]  # (N, 6, P, C)
    moved = np.moveaxis(data[:, coarse], 1, 0)  # (7, N, P, C)
    res: dict[str, dict[str, dict[str, float]]] = {}
    for m in METHODS:
        rec = np.moveaxis(itp.interpolate(m, t[coarse], moved, t[fine]), 0, 1)  # type: ignore[arg-type]
        err = rec - truth
        for k, n in enumerate(names):
            e = err[..., k]
            res.setdefault(n, {})[m] = {
                "rmse": float(np.sqrt(np.mean(e**2))),
                "bias": float(np.mean(e)),
                "min_value": float(np.min(rec[..., k])),
            }
    key = "wind_speed_100m_mean"
    gain = 1.0 - res[key]["pchip"]["rmse"] / res[key]["linear"]["rmse"]
    out = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "n_passes": n_pass,
        "n_points": int(data.shape[2]),
        "n_reconstructed_steps_per_pass": len(fine),
        "unit_note": "m/s, grados C y Pa; error = reconstruido - real; malla 6 h -> 3 h",
        "main": {n: res[n] for n in MAIN},
        "all": res,
        "decision": {
            "rule": f"PCHIP solo si mejora el RMSE de {key} en más de {THRESHOLD:.0%}",
            "pchip_rmse_improvement_vs_linear": gain,
            "chosen": "pchip" if gain > THRESHOLD else "linear",
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"pasadas={n_pass} decisión={out['decision']['chosen']} mejora PCHIP={gain:.2%}")
    for n in MAIN:
        print(n, {m: (round(v["rmse"], 4), round(v["bias"], 4)) for m, v in res[n].items()})


if __name__ == "__main__":
    main()
