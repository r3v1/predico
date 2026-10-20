# ruff: noqa: PLR2004
"""Redacción del token de ENTSO-E en textos, excepciones y logs.

La API recibe el token como parámetro de la URL (`securityToken=...`), así que `httpx`,
`requests` y `entsoe-py` lo incluyen en mensajes de error y en logs de nivel INFO/DEBUG.
"""

import logging
import re

MASK = "<token-redactado>"
_TOKEN_RE = re.compile(r"(securityToken=)[^&\s'\"<>)]+", re.IGNORECASE)
_secrets: set[str] = set()


def register_secret(secret: str) -> None:
    """Registra el valor literal del token para borrarlo aunque aparezca fuera de una URL."""
    if len(secret) >= 8:
        _secrets.add(secret)


def redact(text: str) -> str:
    text = _TOKEN_RE.sub(rf"\1{MASK}", text)
    for secret in _secrets:
        text = text.replace(secret, MASK)
    return text


def install_log_redaction() -> None:
    """Todo registro de logging pasa por `redact` (cubre `httpx`, `httpcore` y `entsoe`)."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        record.msg = redact(record.getMessage())
        record.args = None
        return record

    factory._redacting = True  # pyright: ignore[reportFunctionMemberAccess]
    logging.setLogRecordFactory(factory)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
