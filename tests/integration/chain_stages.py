"""Etapas de la cadena entera con datos sintéticos (`run_chain`) y sus salidas (`Chain`).

Orden: MaStR (ZIP sintético → plata + centroides) → NWP (origen falso → bronce Icechunk local →
plata Parquet por clúster) → ENTSO-E (API falsa por transporte HTTP → bronce XML → plata Delta)
→ tabla oro (CLI de `features`) → backtest. Cada etapa usa el código real de producción; solo se
fabrican las entradas en la frontera más baja posible (ver `chain_synth.py`).
"""

import datetime as dt
import time
from pathlib import Path

import httpx
import numpy as np
import polars as pl
from chain_synth import (
    DOMAIN,
    EXPORT_DATE,
    FETCH_END,
    FETCH_START,
    Chain,
    FakeEntsoe,
    issue_dates,
    nwp_source,
    write_mastr_export,
)

from predico50hz.features.__main__ import main as features_main
from predico50hz.ingest.dynamical.bronze import BronzeStore
from predico50hz.ingest.dynamical.config import VARIABLES
from predico50hz.ingest.dynamical.pipeline import Config, ReadResult, write_stage
from predico50hz.ingest.dynamical.source import read_cube
from predico50hz.ingest.entsoe import silver as entsoe_silver
from predico50hz.ingest.entsoe import validate as entsoe_validate
from predico50hz.ingest.entsoe.client import EntsoeClient
from predico50hz.ingest.entsoe.datasets import DATASETS
from predico50hz.ingest.entsoe.pipeline import fetch_dataset
from predico50hz.ingest.ifs.extract import load_points
from predico50hz.ingest.mastr.pipeline import run as mastr_run


def _stage_mastr(c: Chain) -> None:
    zip_path = c.root / f"Gesamtdatenexport_{EXPORT_DATE}_26.1.zip"
    c.n_units_zone = write_mastr_export(zip_path)
    # k fijado: la búsqueda automática de k (15-30) necesita miles de unidades.
    c.mastr_report = mastr_run(
        zip_path, EXPORT_DATE, c.silver / "mastr", c.validation, c.centroids, k=4
    )


def _stage_nwp(c: Chain) -> None:
    points = load_points(c.centroids)
    cfg = Config(BronzeStore(c.nwp_bronze), c.nwp_silver, points, workers=2, domain=DOMAIN)
    for day in issue_dates():
        src = nwp_source(day)
        cube = read_cube(src, np.datetime64(day.isoformat(), "ns"), VARIABLES, DOMAIN, workers=2)
        c.nwp_metrics.append(write_stage(ReadResult(cube, 0.0, 0.0), src, cfg, day))


def _stage_entsoe(c: Chain) -> None:
    cap_total = pl.read_delta(str(c.silver / "mastr" / "capacity_total"))
    fake = c.fake = FakeEntsoe(cap_total)
    client = EntsoeClient(
        "token-falso-0000",
        transport=httpx.MockTransport(fake.handler),
        min_interval=0.0,
        sleep=lambda _s: None,
    )
    bronze = c.root / "bronze" / "entsoe"
    silver = c.silver / "entsoe"
    gen = DATASETS["generation"]
    try:
        # Primera descarga de julio con valores a la mitad; la segunda (completa) la corrige.
        fake.scale = 0.5
        first = fetch_dataset(
            client, gen, dt.date(2026, 7, 1), dt.date(2026, 8, 1), bronze, "20260802T000000Z"
        )
        fake.scale = 1.0
        second = fetch_dataset(client, gen, FETCH_START, FETCH_END, bronze, "20260803T000000Z")
        c.entsoe_info["generation"] = [first, second]
        fc = DATASETS["forecast"]
        c.entsoe_info["forecast"] = [
            fetch_dataset(client, fc, FETCH_START, FETCH_END, bronze, "20260803T000000Z")
        ]
    finally:
        client.close()
    for name in ("generation", "forecast"):
        entsoe_silver.build_silver(name, bronze, silver)
        c.entsoe_reports[name] = entsoe_validate.validate(name, bronze, silver, c.validation)


def _stage_gold(c: Chain) -> None:
    c.gold_rc = features_main(
        [
            "--gold", str(c.gold),
            "--validation", str(c.validation),
            "--nwp", str(c.nwp_silver),
            "--generation", str(c.silver / "entsoe" / "generation"),
            "--cap-cluster", str(c.silver / "mastr" / "capacity_cluster"),
            "--cap-total", str(c.silver / "mastr" / "capacity_total"),
            "--centroids", str(c.centroids),
        ]
    )  # fmt: skip


def run_chain(root: Path) -> Chain:
    """Ejecuta las cuatro etapas de ingesta y construcción de la oro bajo `root`."""
    c = Chain(root)
    for name, stage in (
        ("mastr", _stage_mastr),
        ("nwp", _stage_nwp),
        ("entsoe", _stage_entsoe),
        ("gold", _stage_gold),
    ):
        t0 = time.perf_counter()
        stage(c)
        c.timings[name] = time.perf_counter() - t0
    return c
