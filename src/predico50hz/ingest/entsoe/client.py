# ruff: noqa: PLR2004, PLR0913
"""Cliente HTTP mínimo de la API REST de ENTSO-E que devuelve la respuesta cruda (bytes).

No se usa `EntsoeRawClient` de entsoe-py porque devuelve `response.text` (rompe los ZIP),
no reintenta con backoff por código HTTP y deja el token en las excepciones. Aquí todo error
sale ya redactado y encadenado con `from None`.
"""

import datetime as dt
import re
import time
from collections.abc import Callable

import httpx

from predico50hz.ingest.entsoe.redact import redact, register_secret

API_URL = "https://web-api.tp.entsoe.eu/api"
MIN_INTERVAL_S = 0.2  # 300 peticiones/min, por debajo del límite de ~400/min
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_NO_DATA_RE = re.compile(rb"No matching data found", re.IGNORECASE)
MAX_RETRIES_400 = (
    2  # ENTSO-E devuelve 400 transitorios (Acknowledgement) con la misma petición válida
)
_REASON_RE = re.compile(r"<Reason>(.*?)</Reason>", re.DOTALL)


class EntsoeError(Exception):
    """Error de la API; el mensaje nunca contiene el token."""


class NoDataError(EntsoeError):
    """La API contestó que no hay datos para esa ventana (no es un fallo)."""


def fmt_time(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y%m%d%H%M")


class EntsoeClient:
    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 6,
        timeout: float = 120.0,
        min_interval: float = MIN_INTERVAL_S,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        register_secret(api_key)
        self._key = api_key
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._max_retries = max_retries
        self._min_interval = min_interval
        self._sleep = sleep
        self._last = 0.0
        self.requests = 0
        self.retries = 0
        self.bytes_raw = 0

    def close(self) -> None:
        self._http.close()

    def fetch(self, params: dict[str, str], start: dt.datetime, end: dt.datetime) -> bytes:
        """Una petición con reintentos; devuelve XML o ZIP crudo. `NoDataError` si no hay datos."""
        query = {
            **params,
            "securityToken": self._key,
            "periodStart": fmt_time(start),
            "periodEnd": fmt_time(end),
        }
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            delay = min(2.0**attempt, 60.0)
            try:
                r = self._http.get(API_URL, params=query)
            except httpx.HTTPError as e:
                problem = f"{type(e).__name__}: {redact(str(e))}"
            else:
                if r.status_code == 200:
                    self.bytes_raw += len(r.content)
                    if _NO_DATA_RE.search(r.content[:4096]):
                        raise NoDataError("sin datos")
                    return r.content
                if r.status_code == 401:
                    raise EntsoeError("401: token rechazado por ENTSO-E") from None
                if _NO_DATA_RE.search(r.content[:4096]):
                    raise NoDataError("sin datos")
                reason = _REASON_RE.search(r.text)
                body = redact(reason.group(1) if reason else r.text[:300].replace("\n", " "))
                problem = f"HTTP {r.status_code}: {body}"
                transient = r.status_code in RETRY_STATUS or (
                    r.status_code == 400 and attempt < MAX_RETRIES_400
                )
                if not transient:
                    raise EntsoeError(problem) from None
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            if attempt == self._max_retries:
                raise EntsoeError(f"agotados los reintentos ({problem})") from None
            self.retries += 1
            self._sleep(delay)
        raise AssertionError("inalcanzable")  # pragma: no cover
