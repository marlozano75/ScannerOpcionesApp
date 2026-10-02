"""Detección de qué TWS/Gateway está abierto (simulada o real) por su puerto. No importa ib_async."""
from __future__ import annotations

import logging
import socket

from scanner_opciones.config.settings import IbkrSettings
from scanner_opciones.domain.enums import AccountMode

log = logging.getLogger(__name__)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_mode(s: IbkrSettings, timeout: float = 0.5) -> AccountMode:
    """Modo cuyo puerto responde. Si responden los dos o ninguno, se respeta el modo configurado."""
    live = port_open(s.host, s.ports.live, timeout)
    paper = port_open(s.host, s.ports.paper, timeout)
    if live != paper:
        mode = AccountMode.LIVE if live else AccountMode.PAPER
        log.info("Puerto %s abierto: modo %s", s.ports.live if live else s.ports.paper, mode.value)
        return mode
    log.info("%s: se usa el modo configurado (%s)",
             "Responden los dos puertos" if live else "No responde ningún puerto", s.mode.value)
    return s.mode
