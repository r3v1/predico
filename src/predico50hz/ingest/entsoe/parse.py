"""Parseo de documentos XML de ENTSO-E (GL_MarketDocument y Publication_MarketDocument).

Cada punto sale con su serie (`psr_type`, `sequence`), su resolución y los metadatos del
documento (`doc_created`, `doc_revision`). Las series de tipo A03 omiten las posiciones cuyo
valor no cambia: se materializan arrastrando el último valor y se marcan con `implied=True`.
"""

import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import polars as pl

POINT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "psr_type": pl.String,
    "sequence": pl.Int32,
    "ts_utc": pl.Datetime("us", "UTC"),
    "value": pl.Float64,
    "resolution": pl.String,
    "resolution_min": pl.Int32,
    "implied": pl.Boolean,
    "doc_created": pl.Datetime("us", "UTC"),
    "doc_revision": pl.Int32,
}
_RES_RE = re.compile(r"^PT(\d+)M$")
_HOUR_RE = re.compile(r"^PT(\d+)H$")


def resolution_minutes(res: str) -> int | None:
    """`PT15M` -> 15, `PT60M`/`PT1H` -> 60, `P1D` -> 1440; `P1Y` y similares -> None."""
    if m := _RES_RE.match(res):
        return int(m[1])
    if m := _HOUR_RE.match(res):
        return int(m[1]) * 60
    return 1440 if res == "P1D" else None


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).astimezone(dt.UTC)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el: ET.Element | None, name: str) -> str | None:
    c = _child(el, name) if el is not None else None
    return c.text.strip() if c is not None and c.text else None


def iter_documents(raw: bytes) -> list[bytes]:
    """Un XML suelto, o los XML de un ZIP (la API devuelve ZIP si hay varios documentos)."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return [z.read(n) for n in z.namelist() if n.lower().endswith(".xml")]
    return [raw]


def parse_document(xml: bytes) -> pl.DataFrame:
    """Puntos de un documento; vacío si es un Acknowledgement o no trae series."""
    root = ET.fromstring(xml)
    created = _text(root, "createdDateTime")
    revision = _text(root, "revisionNumber")
    rows: list[dict[str, object]] = []
    for series in (c for c in root if _local(c.tag) == "TimeSeries"):
        mkt = _child(series, "MktPSRType")
        psr = _text(mkt, "psrType")
        seq = _text(series, "classificationSequence_AttributeInstanceComponent.position")
        a03 = _text(series, "curveType") == "A03"
        for period in (c for c in series if _local(c.tag) == "Period"):
            interval = _child(period, "timeInterval")
            start = _ts(_text(interval, "start") or "")
            end = _ts(_text(interval, "end") or "")
            res = _text(period, "resolution") or ""
            minutes = resolution_minutes(res)
            values: dict[int, float] = {}
            for p in (c for c in period if _local(c.tag) == "Point"):
                amount = _text(p, "quantity") or _text(p, "price.amount")
                if amount is not None:
                    values[int(_text(p, "position") or 0)] = float(amount)
            if minutes is None:  # periodos anuales: un único punto, sin malla
                n = max(values, default=0)
                step = None
            else:
                step = dt.timedelta(minutes=minutes)
                n = round((end - start) / step)
            last: float | None = None
            for pos in range(1, n + 1):
                implied = pos not in values
                if implied:
                    if not a03 or last is None:
                        continue
                    v = last
                else:
                    v = values[pos]
                last = v
                rows.append(
                    {
                        "psr_type": psr,
                        "sequence": int(seq) if seq else None,
                        "ts_utc": start + (step or dt.timedelta(0)) * (pos - 1),
                        "value": v,
                        "resolution": res,
                        "resolution_min": minutes,
                        "implied": implied,
                        "doc_created": _ts(created) if created else None,
                        "doc_revision": int(revision) if revision else None,
                    }
                )
    return pl.DataFrame(rows, schema=POINT_SCHEMA)


def parse_raw(raw: bytes) -> pl.DataFrame:
    frames = [parse_document(x) for x in iter_documents(raw)]
    return pl.concat(frames) if frames else pl.DataFrame(schema=POINT_SCHEMA)
