"""Espera de red para las unidades systemd de usuario.

`network-online.target` del gestor de usuario no espera a la red del sistema (portátil que
despierta o arranca sin DNS): cada job comprueba antes que sus hosts resuelven y aceptan TCP.
"""

import socket
import time
from collections.abc import Callable

HOSTS = {
    "nwp-daily": ["stac.dynamical.org"],
    "entsoe-daily": ["web-api.tp.entsoe.eu"],
    "entsoe-hourly": ["web-api.tp.entsoe.eu"],
}


def reachable(host: str, port: int = 443, timeout_s: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def wait_online(  # noqa: PLR0913
    hosts: list[str],
    timeout_s: float,
    *,
    interval_s: float = 10.0,
    check: Callable[[str], bool] = reachable,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """True en cuanto todos los `hosts` son alcanzables; False si se agota `timeout_s`."""
    deadline = clock() + timeout_s
    while True:
        if all(check(h) for h in hosts):
            return True
        if clock() + interval_s > deadline:
            return False
        sleep(interval_s)
