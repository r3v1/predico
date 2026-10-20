"""Locks de fichero (`fcntl.flock`): un solo escritor por almacén aunque los timers se solapen.

El lock se libera solo si el proceso muere (lo hace el kernel), así que no quedan locks huérfanos.
`flock` no es fiable sobre NFS: `PREDICO_LOCK_DIR` debe estar en un disco local.
"""

import fcntl
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

NWP_BRONZE = "nwp-bronze"  # Icechunk de bronce NWP + plata NWP
ENTSOE = "entsoe"  # bronce y plata de ENTSO-E
GOLD = "gold"  # tabla oro de variables
OPS = "ops-tables"  # registro de ejecuciones y tabla de disponibilidad


class LockTimeoutError(RuntimeError):
    pass


@contextmanager
def file_lock(
    lock_dir: Path, name: str, timeout_s: float = 1800.0, poll_s: float = 0.5
) -> Generator[None]:
    """Lock exclusivo y bloqueante con tiempo máximo de espera."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"lock '{name}' ocupado tras {timeout_s:.0f} s"
                    ) from None
                time.sleep(poll_s)
        yield
    finally:
        os.close(fd)  # cerrar el descriptor libera el flock
