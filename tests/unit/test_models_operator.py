"""Referencia del operador: versión `latest` (plata) y `first_published` (bronce por descarga)."""

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from predico50hz.ingest.entsoe import bronze
from predico50hz.models.baselines import Operator, load_operator_first_published_mw

UTC = dt.UTC
T0 = dt.datetime(2026, 10, 20, tzinfo=UTC)


def xml(psr: str, start: dt.datetime, values: list[float]) -> bytes:
    pts = "".join(
        f"<Point><position>{k + 1}</position><quantity>{v}</quantity></Point>"
        for k, v in enumerate(values)
    )
    end = start + dt.timedelta(minutes=15 * len(values))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
<revisionNumber>1</revisionNumber><createdDateTime>2026-01-01T00:00:00Z</createdDateTime>
<TimeSeries><curveType>A01</curveType><MktPSRType><psrType>{psr}</psrType></MktPSRType>
<Period><timeInterval><start>{start:%Y-%m-%dT%H:%MZ}</start><end>{end:%Y-%m-%dT%H:%MZ}</end>
</timeInterval><resolution>PT15M</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>
""".encode()


def fetch(
    root: Path, when: dt.datetime, values: list[float], psrs: tuple[str, ...] = ("B18", "B19")
) -> None:
    """Una descarga con `len(values)` cuartos desde T0; cada tecnología aporta `v` y `v / 10`."""
    stamp = bronze.stamp(when)
    for psr in psrs:
        k = 1 if psr == "B18" else 0.1
        raw = xml(psr, T0, [v * k for v in values])
        bronze.write_chunk(root, "forecast", stamp, psr, T0, T0 + dt.timedelta(days=1), raw)


def grid(n: int) -> pl.DataFrame:
    return pl.DataFrame(
        {"ts_utc": [T0 + dt.timedelta(minutes=15 * k) for k in range(n)]},
        schema={"ts_utc": pl.Datetime("us", "UTC")},
    )


def test_first_published_keeps_first_version_per_quarter(tmp_path: Path) -> None:
    fetch(tmp_path, T0 + dt.timedelta(hours=1), [100, 200])  # primera: 2 cuartos
    fetch(tmp_path, T0 + dt.timedelta(hours=2), [150, 250, 300])  # revisa 2 y añade 1
    op = load_operator_first_published_mw(tmp_path)
    assert op["op_mw"].to_list() == pytest.approx([110.0, 220.0, 330.0])


def test_first_published_ignores_fetch_missing_one_technology(tmp_path: Path) -> None:
    fetch(tmp_path, T0 + dt.timedelta(hours=1), [100, 200], psrs=("B18",))
    fetch(tmp_path, T0 + dt.timedelta(hours=2), [150, 250])
    op = load_operator_first_published_mw(tmp_path)
    assert op["op_mw"].to_list() == pytest.approx([165.0, 275.0])


def test_first_published_empty_bronze(tmp_path: Path) -> None:
    assert load_operator_first_published_mw(tmp_path).is_empty()


def test_operator_first_published_predict_and_params(tmp_path: Path) -> None:
    fetch(tmp_path, T0 + dt.timedelta(hours=1), [100, 200])
    m = Operator(version="first_published", bronze=tmp_path)
    p = m.predict(grid(3)).q50
    assert p[:2] == pytest.approx([110.0, 220.0])
    assert np.isnan(p[2])  # sin versión publicada: NaN, no se inventa
    assert m.params()["version"] == "first_published"
    assert m.params()["bronze"] == str(tmp_path)


def test_operator_latest_is_default_and_reads_silver(tmp_path: Path) -> None:
    silver = tmp_path / "silver"
    rows = [(T0, "B18", 1.0), (T0, "B19", 2.0)]
    pl.DataFrame(
        rows,
        schema={
            "ts_utc": pl.Datetime("us", "UTC"),
            "psr_type": pl.String,
            "forecast_mw": pl.Float64,
        },
        orient="row",
    ).write_delta(str(silver))
    m = Operator(source=silver)
    assert m.version == "latest"
    assert "bronze" not in m.params()
    assert m.params()["version"] == "latest"
    assert m.predict(grid(1)).q50 == pytest.approx([3.0])


def test_operator_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        Operator(version="otra")  # type: ignore[arg-type]
