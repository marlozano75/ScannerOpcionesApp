"""Detección de qué TWS o IB Gateway está abierto (simulada o real) por su puerto. No importa ib_async."""
from __future__ import annotations

import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from scanner_opciones.config.settings import IbkrSettings
from scanner_opciones.domain.enums import AccountMode, Platform

log = logging.getLogger(__name__)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class Endpoint:
    platform: Platform
    mode: AccountMode
    port: int
    active: bool

    @property
    def key(self) -> str:
        return f"{self.platform.value}:{self.mode.value}"


def scan_endpoints(s: IbkrSettings, timeout: float = 0.5) -> list[Endpoint]:
    """Los cuatro destinos posibles (TWS y IB Gateway, real y simulada) y cuáles responden ahora."""
    combos = [(p, m) for p in Platform for m in (AccountMode.PAPER, AccountMode.LIVE)]
    with ThreadPoolExecutor(max_workers=len(combos)) as pool:
        opened = list(pool.map(lambda c: port_open(s.host, s.ports.get(*c), timeout), combos))
    return [Endpoint(p, m, s.ports.get(p, m), ok) for (p, m), ok in zip(combos, opened)]


def choose_endpoint(s: IbkrSettings, endpoints: list[Endpoint]) -> tuple[Platform, AccountMode]:
    """Destino al que conectar: el único que responde; si responden varios, el configurado si está entre ellos
    (si no, el primero: TWS antes que Gateway, simulada antes que real); si no responde ninguno, el configurado."""
    active = [e for e in endpoints if e.active]
    configured = (s.platform, s.mode)
    if not active or any((e.platform, e.mode) == configured for e in active):
        return configured
    return active[0].platform, active[0].mode


def detect_endpoint(s: IbkrSettings, timeout: float = 0.5) -> tuple[Platform, AccountMode]:
    endpoints = scan_endpoints(s, timeout)
    platform, mode = choose_endpoint(s, endpoints)
    log.info("Destinos activos: %s; se usa %s (%s)",
             ", ".join(f"{e.platform.value} {e.mode.value} :{e.port}" for e in endpoints if e.active) or "ninguno",
             platform.value, mode.value)
    return platform, mode


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
