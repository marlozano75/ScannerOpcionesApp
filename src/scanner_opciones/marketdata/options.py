"""Puerto `OptionDataProvider`: cadena de opciones, cotizaciones de opciones, precios y extras de mercado
(IV a 30 días y ex-dividendo) de un proveedor externo. Sustituye a IBKR en estas consultas; la cuenta, las
posiciones, el margen what-if, el sector y el VIX siguen siendo del broker.

Nada fuera de las implementaciones reales debe importar el SDK del proveedor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol, Sequence

from scanner_opciones.domain.models import OptionQuote, VixData

# (vencimiento, strike redondeado a 2 decimales) -> símbolo de streaming del proveedor
OptionListing = dict[tuple[date, float], str]


def listing_key(expiry: date, strike: float) -> tuple[date, float]:
    return expiry, round(float(strike), 2)


@dataclass(frozen=True)
class MarketExtras:
    iv30: Optional[float] = None            # IV a 30 días, fracción (0,28 = 28 %)
    ex_dividend: Optional[date] = None      # última / próxima fecha ex-dividendo conocida


class OptionDataProvider(Protocol):
    async def get_put_listing(self, ticker: str) -> OptionListing:
        """Puts que existen realmente (estándar, 100 acciones). Lanza `OptionDataError` si el proveedor falla."""
        ...

    async def get_option_quotes(self, symbols: Sequence[str]) -> dict[str, OptionQuote]:
        """Cotizaciones por símbolo de streaming; los que no responden no aparecen. Lanza `OptionDataError`."""
        ...

    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        """Lanza `PriceError` si el proveedor falla."""
        ...

    async def get_market_extras(self, tickers: list[str]) -> dict[str, MarketExtras]:
        """Lanza `VolatilityError` si el proveedor falla."""
        ...

    async def get_vix(self, history_days: int, futures_ahead: int) -> VixData:
        """VIX (último cierre y precio en vivo) y los próximos futuros. Lanza `OptionDataError` si falla."""
        ...


@dataclass
class FakeOptionData:
    """Proveedor en memoria para los tests."""
    listings: dict[str, OptionListing] = field(default_factory=dict)
    quotes: dict[str, OptionQuote] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    extras: dict[str, MarketExtras] = field(default_factory=dict)
    vix: VixData = field(default_factory=VixData)
    error: Optional[Exception] = None
    calls: list[tuple] = field(default_factory=list)

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def get_put_listing(self, ticker: str) -> OptionListing:
        self.calls.append(("get_put_listing", ticker))
        self._maybe_fail()
        return dict(self.listings.get(ticker, {}))

    async def get_option_quotes(self, symbols: Sequence[str]) -> dict[str, OptionQuote]:
        self.calls.append(("get_option_quotes", tuple(symbols)))
        self._maybe_fail()
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        self.calls.append(("get_prices", tuple(tickers)))
        self._maybe_fail()
        return {t: self.prices[t] for t in tickers if t in self.prices}

    async def get_market_extras(self, tickers: list[str]) -> dict[str, MarketExtras]:
        self.calls.append(("get_market_extras", tuple(tickers)))
        self._maybe_fail()
        return {t: self.extras[t] for t in tickers if t in self.extras}

    async def get_vix(self, history_days: int, futures_ahead: int) -> VixData:
        self.calls.append(("get_vix", history_days, futures_ahead))
        self._maybe_fail()
        return VixData(self.vix.current, self.vix.last_closes[-history_days:], self.vix.futures[:futures_ahead],
                       self.vix.updated_at)
