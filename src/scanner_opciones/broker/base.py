"""Puerto del broker. El dominio y los jobs solo dependen de esta interfaz."""
from __future__ import annotations

import logging
import sys
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Iterator, Optional, Protocol, Sequence

from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, UnderlyingQuote, VixData,
)

log = logging.getLogger(__name__)


class ChainTally:
    """Cuántas listas de contratos (cadenas) se piden al proveedor y cuántas se reutilizan, y desde dónde."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fetched = 0
        self.reused = 0
        self.callers: Counter[str] = Counter()

    def note(self, fetched: bool) -> None:
        if fetched:
            self.fetched += 1
            self.callers[_caller()] += 1
        else:
            self.reused += 1

    def summary(self) -> str:
        top = ", ".join(f"{c} ×{n}" for c, n in self.callers.most_common(3))
        return f"Cadenas de opciones ({self.name}): {self.fetched} pedidas al proveedor, {self.reused} reutilizadas de la caché" + (f" [{top}]" if top else "")


_ORIGIN: ContextVar[Optional[ChainTally]] = ContextVar("chain_origin", default=None)
_UNLABELLED = ChainTally("sin origen declarado")
_UNLABELLED_EVERY = 100


def _caller() -> str:
    """Funciones de la app (servicio y jobs) que hay detrás de la petición, de dentro a fuera (máximo tres)."""
    names: list[str] = []
    frame = sys._getframe(2)
    while frame is not None and len(names) < 3:
        mod = frame.f_globals.get("__name__", "")
        if mod.startswith(("scanner_opciones.app", "scanner_opciones.jobs", "scanner_opciones.ui")):
            names.append(f"{mod.rsplit('.', 1)[-1]}.{frame.f_code.co_name}")
        frame = frame.f_back
    return ">".join(names) or "?"


@contextmanager
def chain_origin(name: str) -> Iterator[ChainTally]:
    """Marca de dónde vienen las peticiones de cadenas de este bloque y deja al salir una línea INFO con el recuento.
    Sirve para saber qué trabajo pide cadenas (y cuántas): cada petición es una llamada a tastytrade."""
    tally = ChainTally(name)
    token = _ORIGIN.set(tally)
    try:
        yield tally
    finally:
        _ORIGIN.reset(token)
        if tally.fetched or tally.reused:
            log.info(tally.summary())


def note_chain(fetched: bool) -> None:
    """La pasarela anota cada lista de contratos pedida (`fetched`) o reutilizada de la caché."""
    tally = _ORIGIN.get()
    if tally is not None:
        tally.note(fetched)
        return
    _UNLABELLED.note(fetched)
    if _UNLABELLED.fetched and _UNLABELLED.fetched % _UNLABELLED_EVERY == 0 and fetched:
        log.info(_UNLABELLED.summary())


class BrokerGateway(Protocol):
    """Todos los métodos son async. Errores: BrokerError y subclases."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def get_account_summary(self) -> AccountSummary: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        """(sector, categoría) según IBKR."""

    async def get_underlying_price(self, ticker: str) -> Optional[float]: ...

    async def get_underlying_quotes(self, tickers: Sequence[str]) -> dict[str, UnderlyingQuote]:
        """Precio y IV actuales de varios subyacentes de una vez. Sin ningún dato: no aparecen."""

    async def get_option_chain(self, ticker: str) -> OptionChain: ...

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        """None si no aplica / no hay dividendo próximo."""

    async def get_days_to_ex_dividend_many(self, tickers: Sequence[str]) -> dict[str, Optional[int]]:
        """Como `get_days_to_ex_dividend` para varios tickers a la vez (una sola espera). Los que
        fallan no aparecen; los que no tienen dividendo próximo traen None."""

    async def qualify_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        """Devuelve solo los contratos que existen en IBKR, con `con_id` informado."""

    async def get_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        """Cotizaciones por lotes; los contratos sin datos no aparecen o traen campos None."""

    async def get_bid_sizes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, int]:
        """Tamaño del bid según TWS (el de tastytrade es entre 5 y 10 veces menor). Los contratos sin dato no aparecen."""

    async def what_if_margin(self, contract: OptionContract, quantity: int = 1) -> Optional[float]:
        """Cambio de margen inicial al vender `quantity` contratos (what-if; nunca envía orden)."""

    async def get_vix_data(self, history_days: int, futures_ahead: int) -> VixData: ...

    def historical_request_counts(self) -> dict[str, int]:
        """Peticiones históricas hechas desde que arrancó, acumuladas por tipo (precio, iv, vix, futuros_vix)."""

    def pacing_wait_seconds(self) -> float:
        """Segundos que lleva por delante la espera por el límite de peticiones históricas de IBKR (0 si no hay)."""
