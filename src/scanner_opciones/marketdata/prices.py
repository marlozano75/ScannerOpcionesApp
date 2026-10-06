"""Puerto `PriceProvider`: segunda fuente del precio del subyacente para contrastar el de IBKR.

IBKR a veces devuelve un precio extraño (p. ej. CBNK 51,9 con cierre de 40,02: el punto medio de un
bid/ask sin liquidez). Si el de IBKR se aleja más de `max_deviation_pct` del de esta fuente, se toma el de
la fuente. Si la fuente falla o no cubre el ticker, se conserva el de IBKR (no hay con qué juzgarlo).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Optional, Protocol

from scanner_opciones.domain.errors import PriceError
from scanner_opciones.domain.models import UnderlyingQuote

log = logging.getLogger(__name__)


class PriceProvider(Protocol):
    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        """Último precio de cada ticker en pocas peticiones. Los que no cubre no aparecen.
        Lanza `PriceError` si el proveedor falla."""
        ...


@dataclass
class FakePrices:
    """Proveedor en memoria para los tests."""
    prices: dict[str, float] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[list[str]] = field(default_factory=list)

    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        self.calls.append(list(tickers))
        if self.error is not None:
            raise self.error
        return {t: self.prices[t] for t in tickers if t in self.prices}


def reconcile_price(
    ticker: str, ibkr: Optional[float], external: Optional[float], max_deviation_pct: float
) -> Optional[float]:
    """Precio de IBKR salvo que se aleje más de `max_deviation_pct` % del externo; entonces, el externo."""
    if ibkr is None or not external or external <= 0:
        return ibkr
    deviation = abs(ibkr / external - 1) * 100
    if deviation <= max_deviation_pct:
        return ibkr
    log.warning("%s: precio de IBKR %.4g muy distinto del externo %.4g (%.1f %%); se usa el externo",
                ticker, ibkr, external, deviation)
    return external


async def reconcile_quotes(
    provider: Optional[PriceProvider], quotes: dict[str, UnderlyingQuote], max_deviation_pct: float
) -> dict[str, UnderlyingQuote]:
    """Corrige los precios de IBKR que no cuadran con la fuente externa (una petición para todos)."""
    tickers = [t for t, q in quotes.items() if q.price is not None]
    if provider is None or not tickers:
        return quotes
    try:
        external = await provider.get_prices(tickers)
    except PriceError as exc:
        log.warning("Precios externos no disponibles, se usan los de IBKR sin contrastar: %s", exc)
        return quotes
    out = dict(quotes)
    for t in tickers:
        price = reconcile_price(t, quotes[t].price, external.get(t), max_deviation_pct)
        if price != quotes[t].price:
            out[t] = replace(quotes[t], price=price)
    return out
