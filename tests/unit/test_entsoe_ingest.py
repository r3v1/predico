# ruff: noqa: PLR2004, PLR0913
"""Pruebas sin red de la ingesta de ENTSO-E: parseo, malla, plata idempotente y redacción."""

import datetime as dt
import gzip
import io
import logging
import zipfile
from pathlib import Path

import httpx
import polars as pl
import pytest
from deltalake import DeltaTable

from predico50hz.ingest.entsoe import bronze, silver, validate
from predico50hz.ingest.entsoe.client import EntsoeClient, EntsoeError, NoDataError
from predico50hz.ingest.entsoe.datasets import DATASETS, windows
from predico50hz.ingest.entsoe.parse import parse_document, parse_raw, resolution_minutes
from predico50hz.ingest.entsoe.pipeline import fetch_dataset
from predico50hz.ingest.entsoe.redact import install_log_redaction, redact, register_secret

TOKEN = "s3cr3t-token-1234"
UTC = dt.UTC
ACK = (
    b"<Acknowledgement_MarketDocument><Reason><text>No matching data found</text></Reason>"
    b"</Acknowledgement_MarketDocument>"
)


def doc(
    points: dict[int, float],
    *,
    psr: str = "B19",
    start: str = "2024-01-01T00:00Z",
    end: str = "2024-01-01T02:00Z",
    res: str = "PT15M",
    created: str = "2026-10-16T12:45:19Z",
    revision: int = 1,
    curve: str = "A03",
    tag: str = "quantity",
    extra: str = "",
) -> bytes:
    pts = "".join(
        f"<Point><position>{k}</position><{tag}>{v}</{tag}></Point>" for k, v in points.items()
    )
    mkt = f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>" if psr else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
<revisionNumber>{revision}</revisionNumber><createdDateTime>{created}</createdDateTime>
<TimeSeries><curveType>{curve}</curveType>{extra}{mkt}
<Period><timeInterval><start>{start}</start><end>{end}</end></timeInterval>
<resolution>{res}</resolution>{pts}</Period></TimeSeries></GL_MarketDocument>""".encode()


def pts(df: pl.DataFrame, **extra: object) -> pl.DataFrame:
    """Añade las columnas de bronce que `load_points` pone al leer."""
    return df.with_columns(
        fetched_at=pl.lit(extra.get("fetched_at", dt.datetime(2026, 10, 16, tzinfo=UTC))).cast(
            pl.Datetime("us", "UTC")
        ),
        fallback=pl.lit(False),
    )


# --- parseo ------------------------------------------------------------------------
def test_parse_metadata_and_a03_carry() -> None:
    # 8 posiciones de 15 min; faltan 3, 4 y 7 (A03: mismo valor que el anterior)
    df = parse_document(doc({1: 10.0, 2: 12.0, 5: 20.0, 6: 21.0, 8: 22.0}))
    assert df.height == 8
    assert df["value"].to_list() == [10.0, 12.0, 12.0, 12.0, 20.0, 21.0, 21.0, 22.0]
    assert df["implied"].to_list() == [False, False, True, True, False, False, True, False]
    assert df["doc_created"][0] == dt.datetime(2026, 10, 16, 12, 45, 19, tzinfo=UTC)
    assert df["doc_revision"][0] == 1
    assert df["ts_utc"][1] == dt.datetime(2024, 1, 1, 0, 15, tzinfo=UTC)


def test_parse_created_datetime_and_revision() -> None:
    df = parse_document(doc({1: 1.0}, created="2024-01-01T09:30:00Z", revision=3))
    assert df["doc_created"][0] == dt.datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    assert df["doc_revision"][0] == 3


def test_parse_non_a03_does_not_carry() -> None:
    df = parse_document(doc({1: 1.0, 3: 3.0}, curve="A01"))
    assert df.height == 2


def test_parse_acknowledgement_and_zip() -> None:
    ack = b"<Acknowledgement_MarketDocument><Reason/></Acknowledgement_MarketDocument>"
    assert parse_document(ack).is_empty()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", doc({1: 1.0}, end="2024-01-01T00:15Z"))
        z.writestr("b.xml", doc({1: 2.0}, start="2024-01-02T00:00Z", end="2024-01-02T00:15Z"))
    assert parse_raw(buf.getvalue()).height == 2


def test_resolution_minutes() -> None:
    assert [resolution_minutes(r) for r in ("PT15M", "PT60M", "PT1H", "P1Y")] == [15, 60, 60, None]


# --- malla y cambio de resolución ----------------------------------------------------
def test_fill_grid_flags_and_gap_length() -> None:
    # curva A01 sin arrastre implícito: faltan las posiciones 3-5 (hueco real de 3 pasos)
    off = parse_document(doc({1: 5.0, 2: 6.0, 6: 9.0, 7: 9.0, 8: 9.5}, psr="B18", curve="A01"))
    on = parse_document(doc({i: 100.0 + i for i in range(1, 9)}, psr="B19", curve="A01"))
    out = silver.build_generation(pts(pl.concat([off, on])))
    assert out.height == 8
    assert out["offshore_src"].to_list() == ["original"] * 2 + ["gap"] * 3 + ["original"] * 3
    assert out["offshore_gap_len"].to_list() == [0, 0, 3, 3, 3, 0, 0, 0]
    assert out["wind_offshore_mw"].to_list()[2:5] == [6.0, 6.0, 6.0]  # arrastre del último valor
    assert out["onshore_src"].unique().to_list() == ["original"]
    assert out["is_gap_filled"].to_list() == [False, False, True, True, True, False, False, False]
    assert out["wind_mw"][2] == pytest.approx(6.0 + 103.0)


def test_fill_grid_a03_is_not_a_gap() -> None:
    off = parse_document(doc({1: 5.0, 4: 7.0}, psr="B18", end="2024-01-01T01:00Z"))
    on = parse_document(doc({1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}, psr="B19", end="2024-01-01T01:00Z"))
    out = silver.build_generation(pts(pl.concat([off, on])))
    assert out["offshore_src"].to_list() == ["original", "a03", "a03", "original"]
    assert out["offshore_gap_len"].sum() == 0


def test_series_starting_late_has_null_before_first_value() -> None:
    off = parse_document(
        doc({1: 5.0}, psr="B18", start="2024-01-01T00:30Z", end="2024-01-01T00:45Z")
    )
    on = parse_document(doc({1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}, psr="B19", end="2024-01-01T01:00Z"))
    out = silver.build_generation(pts(pl.concat([off, on])))
    assert out["offshore_src"].to_list()[:2] == [None, None]
    assert out["wind_mw"].null_count() == 2


def test_resolution_change_hourly_is_expanded_and_detected() -> None:
    hourly = parse_document(
        doc({1: 10.0, 2: 20.0}, psr="B19", res="PT60M", end="2024-01-01T02:00Z")
    )
    quarter = parse_document(
        doc({1: 30.0, 2: 40.0}, psr="B19", start="2024-01-01T02:00Z", end="2024-01-01T02:30Z")
    )
    off = parse_document(
        doc({i: float(i) for i in range(1, 11)}, psr="B18", end="2024-01-01T02:30Z", curve="A01")
    )
    points = pts(pl.concat([hourly, quarter, off]))
    out = silver.build_generation(points)
    assert out.filter(pl.col("onshore_src") == "hourly").height == 8
    assert out["wind_onshore_mw"][:9].to_list() == [10.0] * 4 + [20.0] * 4 + [30.0]
    assert out.height == 10
    later = parse_document(
        doc({1: 1.0, 2: 2.0}, psr="B19", start="2024-03-01T00:00Z", end="2024-03-01T00:30Z")
    )
    segs = validate.resolution_segments(pts(pl.concat([hourly, later])))
    assert [(s["resolution_min"], s["from"][:7]) for s in segs] == [
        (60, "2024-01"),
        (15, "2024-03"),
    ]


def test_latest_fetch_wins_and_revisions_detected() -> None:
    end = "2024-01-01T00:30Z"
    a = pts(
        parse_document(doc({1: 1.0, 2: 2.0}, end=end)),
        fetched_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
    )
    b = pts(
        parse_document(doc({1: 1.0, 2: 2.5}, end=end)),
        fetched_at=dt.datetime(2026, 1, 2, tzinfo=UTC),
    )
    allp = pl.concat([a, b])
    cur = silver.latest(allp, ["psr_type", "ts_utc"])
    assert cur["value"].to_list()[:2] == [1.0, 2.5]
    rev = validate.revisions(allp, "generation")
    assert rev["fetches"] == 2
    assert rev["keys_compared"] == 2
    assert rev["keys_revised"] == 1
    assert rev["max_abs_change"] == pytest.approx(0.5)


def test_forecast_first_seen_keeps_earliest_fetch() -> None:
    end = "2024-01-01T00:15Z"
    a = pts(parse_document(doc({1: 1.0}, end=end)), fetched_at=dt.datetime(2026, 1, 1, tzinfo=UTC))
    b = pts(parse_document(doc({1: 2.0}, end=end)), fetched_at=dt.datetime(2026, 1, 3, tzinfo=UTC))
    out = silver.build_forecast(pl.concat([a, b]))
    assert out["forecast_mw"].to_list() == [2.0]
    assert out["first_seen_at"][0] == dt.datetime(2026, 1, 1, tzinfo=UTC)


# --- bronce, silver idempotente y validación ---------------------------------------------
def _write(root: Path, dataset: str, fetched: str, variant: str, raw: bytes | None) -> None:
    a, b = dt.datetime(2024, 1, 1, tzinfo=UTC), dt.datetime(2024, 1, 2, tzinfo=UTC)
    bronze.write_chunk(root, dataset, fetched, variant, a, b, raw)


def test_bronze_roundtrip_zip_and_nodata(tmp_path: Path) -> None:
    raw = doc({1: 1.0})
    _write(tmp_path, "generation", "20260101T000000Z", "B19", raw)
    _write(tmp_path, "generation", "20260101T000000Z", "B18", None)
    chunks = {c.variant: c for c in bronze.list_chunks(tmp_path, "generation")}
    assert chunks["B18"].nodata
    assert bronze.read_chunk(chunks["B18"]) == b""
    assert bronze.read_chunk(chunks["B19"]) == raw
    assert chunks["B19"].path.name == "B19_20240101_20240102.xml.gz"
    assert gzip.decompress(chunks["B19"].path.read_bytes()) == raw


def test_silver_is_idempotent(tmp_path: Path) -> None:
    br, sv = tmp_path / "bronze", tmp_path / "silver"
    n = 96
    full = {i: 100.0 + i for i in range(1, n + 1)}
    for psr in ("B18", "B19"):
        _write(
            br, "generation", "20260101T000000Z", psr, doc(full, psr=psr, end="2024-01-02T00:00Z")
        )
    first = silver.build_silver("generation", br, sv)
    path = sv / "generation"
    v1 = DeltaTable(str(path)).version()
    second = silver.build_silver("generation", br, sv)
    assert first["written"]
    assert not second["written"]
    assert DeltaTable(str(path)).version() == v1
    df = pl.read_delta(str(path))
    assert df.height == n
    assert df["ts_utc"].is_unique().all()
    # una segunda descarga idéntica tampoco duplica ni cambia la plata
    for psr in ("B18", "B19"):
        _write(
            br, "generation", "20260102T000000Z", psr, doc(full, psr=psr, end="2024-01-02T00:00Z")
        )
    assert not silver.build_silver("generation", br, sv)["written"]
    assert pl.read_delta(str(path)).height == n
    assert validate.schema_issues("generation", df) == []


def test_validate_flags_negative_generation_and_grid_hole() -> None:
    off = parse_document(doc({i: 1.0 for i in range(1, 5)}, psr="B18", end="2024-01-01T01:00Z"))
    on = parse_document(doc({i: 1.0 for i in range(1, 5)}, psr="B19", end="2024-01-01T01:00Z"))
    good = silver.build_generation(pts(pl.concat([off, on])))
    assert validate.schema_issues("generation", good) == []
    bad = good.with_columns(
        wind_onshore_mw=pl.when(pl.int_range(pl.len()) == 1)
        .then(-5.0)
        .otherwise(pl.col("wind_onshore_mw"))
    )
    assert any(i["check"] == "range" for i in validate.schema_issues("generation", bad))
    holed = good.filter(pl.int_range(pl.len()) != 2)
    assert any(i["check"] == "full_grid" for i in validate.schema_issues("generation", holed))
    dup = pl.concat([good, good.head(1)])
    assert any(i["check"] == "unique_key" for i in validate.schema_issues("generation", dup))


def test_gap_runs_report() -> None:
    off = parse_document(
        doc({1: 5.0, 2: 6.0, 6: 9.0}, psr="B18", curve="A01", end="2024-01-01T01:30Z")
    )
    on = parse_document(
        doc({i: 1.0 for i in range(1, 7)}, psr="B19", curve="A01", end="2024-01-01T01:30Z")
    )
    df = silver.build_generation(pts(pl.concat([off, on])))
    runs = validate.gap_runs(df, "offshore")
    assert [(r["steps"], r["hours"]) for r in runs] == [(3, 0.75)]
    assert runs[0]["start"].startswith("2024-01-01T00:30")


# --- troceado, cliente y redacción ---------------------------------------------------------
def test_windows_month_and_year() -> None:
    w = list(windows(dt.date(2024, 11, 15), dt.date(2025, 1, 10), "month"))
    assert [(a.date(), b.date()) for a, b in w] == [
        (dt.date(2024, 11, 15), dt.date(2024, 12, 1)),
        (dt.date(2024, 12, 1), dt.date(2025, 1, 1)),
        (dt.date(2025, 1, 1), dt.date(2025, 1, 10)),
    ]
    y = list(windows(dt.date(2015, 1, 1), dt.date(2017, 1, 1), "year"))
    assert len(y) == 2


def test_redact_url_literal_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    register_secret(TOKEN)
    assert TOKEN not in redact(f"GET https://x/api?securityToken={TOKEN}&a=1")
    assert "a=1" in redact(f"GET https://x/api?securityToken={TOKEN}&a=1")
    assert TOKEN not in redact(f"fallo con {TOKEN} suelto")
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("entsoe").info("url %s", f"https://x?securityToken={TOKEN}")
    assert TOKEN not in caplog.text
    assert "token-redactado" in caplog.text


def _client(handler, **kw) -> EntsoeClient:
    return EntsoeClient(
        TOKEN, transport=httpx.MockTransport(handler), sleep=lambda _s: None, min_interval=0, **kw
    )


def _span() -> tuple[dt.datetime, dt.datetime]:
    return dt.datetime(2024, 1, 1, tzinfo=UTC), dt.datetime(2024, 1, 2, tzinfo=UTC)


def test_client_retries_then_succeeds_and_sends_utc_window() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(503 if len(seen) < 3 else 200, content=b"<x/>")

    c = _client(handler)
    assert c.fetch({"documentType": "A75"}, *_span()) == b"<x/>"
    assert c.retries == 2
    assert len(seen) == 3
    assert seen[0].url.params["periodStart"] == "202401010000"


def test_client_errors_never_leak_token() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"fallo conectando a {req.url}")

    c = _client(boom, max_retries=1)
    with pytest.raises(EntsoeError) as ei:
        c.fetch({}, *_span())
    assert TOKEN not in str(ei.value)
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__

    def bad(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"mala petición {req.url}")

    with pytest.raises(EntsoeError) as ei2:
        _client(bad).fetch({}, *_span())
    assert TOKEN not in str(ei2.value)
    with pytest.raises(EntsoeError, match="401"):
        _client(lambda r: httpx.Response(401)).fetch({}, *_span())


def test_client_no_data_acknowledgement() -> None:
    ack = ACK
    with pytest.raises(NoDataError):
        _client(lambda r: httpx.Response(200, content=ack)).fetch({}, *_span())


def test_fetch_dataset_resume_skips_downloaded(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.params["psrType"])
        return httpx.Response(200, content=doc({1: 1.0}))

    c = _client(handler)
    ds = DATASETS["generation"]
    args = (dt.date(2024, 1, 1), dt.date(2024, 3, 1))
    info = fetch_dataset(c, ds, *args, tmp_path, "20260101T000000Z")
    assert info["chunks_written"] == 4
    assert len(calls) == 4
    info2 = fetch_dataset(c, ds, *args, tmp_path, "20260101T000000Z")
    assert info2["chunks_skipped_resume"] == 4
    assert len(calls) == 4
    # el token no aparece en el bronce ni en la metadata de la ejecución
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert TOKEN.encode() not in p.read_bytes()


def test_capacity_falls_back_to_bidding_zone(tmp_path: Path) -> None:
    ack = ACK

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["in_Domain"] == "10YDE-VE-------2":
            return httpx.Response(200, content=ack)
        return httpx.Response(200, content=doc({1: 5.0}, res="P1Y", end="2025-01-01T00:00Z"))

    c = _client(handler)
    fetch_dataset(
        c,
        DATASETS["capacity"],
        dt.date(2024, 1, 1),
        dt.date(2025, 1, 1),
        tmp_path,
        "20260101T000000Z",
    )
    names = sorted(p.name for p in (tmp_path / "capacity").rglob("*.xml.gz"))
    assert names == [
        "B18-fallback_20240101_20250101.xml.gz",
        "B19-fallback_20240101_20250101.xml.gz",
    ]


def test_silver_forecast_idempotent_with_unordered_joins(tmp_path: Path) -> None:
    br, sv = tmp_path / "bronze", tmp_path / "silver"
    end = "2024-01-02T00:00Z"
    for stamp in ("20260101T000000Z", "20260102T000000Z"):
        for psr in ("B18", "B19"):
            raw = doc({i: float(i) for i in range(1, 97)}, psr=psr, end=end, curve="A01")
            _write(br, "forecast", stamp, psr, raw)
    first = silver.build_silver("forecast", br, sv)
    second = silver.build_silver("forecast", br, sv)
    assert first["written"]
    assert not second["written"]
    df = pl.read_delta(str(sv / "forecast"))
    assert df.height == 192
    assert df["first_seen_at"].n_unique() == 1
    assert validate.schema_issues("forecast", df) == []


def test_client_retries_transient_400_then_succeeds() -> None:
    n = {"i": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] < 3:
            return httpx.Response(400, text="<Acknowledgement_MarketDocument><Reason>x</Reason>")
        return httpx.Response(200, content=b"<ok/>")

    assert _client(flaky).fetch({}, *_span()) == b"<ok/>"
    assert n["i"] == 3


def test_client_retries_gateway_timeout_599() -> None:
    n = {"i": 0}
    body = '{"uuAppErrorMap":{"uu-gateway-router/connectTimeout":{"type":"error"}}}'

    def flaky(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(599, text=body)
        return httpx.Response(200, content=b"<ok/>")

    assert _client(flaky).fetch({}, *_span()) == b"<ok/>"
    assert n["i"] == 2


def test_client_gives_up_on_persistent_400() -> None:
    n = {"i": 0}

    def bad(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        return httpx.Response(400, text="<Reason><code>999</code></Reason>")

    with pytest.raises(EntsoeError, match="999"):
        _client(bad).fetch({}, *_span())
    assert n["i"] == 3
