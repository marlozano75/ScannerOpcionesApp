"""`HybridGateway`: un `BrokerGateway` que saca de un proveedor externo (tastytrade) la cadena de opciones, las
cotizaciones de opciones, los precios de los subyacentes y los ex-dividendos, y deja en el broker real (IBKR)
lo que solo él puede dar: cuenta, posiciones, margen what-if y sector. El VIX y sus futuros también salen del
proveedor.

Si el proveedor falla, cada consulta cae al broker interior y se avisa en el log: la app sigue funcionando
como antes, solo más lenta. No importa `ib_async` ni el SDK del proveedor.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Callable, Optional, Sequence

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import MarketDataSettings
from scanner_opciones.domain.errors import DataUnavailableError, OptionDataError, PriceError, VolatilityError
from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, UnderlyingQuote, VixData,
)
from scanner_opciones.marketdata.options import OptionDataProvider, OptionListing, listing_key

log = logging.getLogger(__name__)

MULTIPLIER = 100   # el proveedor solo lista opciones estándar de 100 acciones


class HybridGateway:
    def __init__(
        self, inner: BrokerGateway, provider: OptionDataProvider, settings: MarketDataSettings,
        now: Callable[[], datetime] = datetime.now, clock: Callable[[], float] = time.monotonic,
        fallback_batch: int = 50,
    ) -> None:
        self.inner = inner
        self.fallback_batch = fallback_batch      # contratos por petición cuando se cae a IBKR (límite de líneas de TWS)
        self.quote_batch_size = settings.quote_batch_size   # contratos por petición de cotizaciones (lo lee el refresco)
        self.provider = provider
        self.s = settings
        self._now = now
        self._clock = clock
        self._listings: dict[str, tuple[float, OptionListing]] = {}   # ticker -> (instante, puts existentes)

    # ---- lo que es siempre del broker ------------------------------------------------------
    async def connect(self) -> None:
        await self.inner.connect()

    async def disconnect(self) -> None:
        await self.inner.disconnect()

    def is_connected(self) -> bool:
        return self.inner.is_connected()

    async def get_account_summary(self) -> AccountSummary:
        return await self.inner.get_account_summary()

    async def get_positions(self) -> list[Position]:
        return await self.inner.get_positions()

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        return await self.inner.get_sector_info(ticker)

    async def what_if_margin(self, contract: OptionContract, quantity: int = 1) -> Optional[float]:
        return await self.inner.what_if_margin(contract, quantity)   # los contratos sin con_id los cualifica IBKR

    async def get_vix_data(self, history_days: int, futures_ahead: int) -> VixData:
        """VIX y futuros de tastytrade; si falla o no trae ningún dato, de IBKR."""
        try:
            vix = await self.provider.get_vix(history_days, futures_ahead)
        except OptionDataError as exc:
            log.warning("VIX no disponible en el proveedor, se pide a IBKR: %s", exc)
            return await self.inner.get_vix_data(history_days, futures_ahead)
        if vix.current is None and not vix.last_closes:
            log.warning("El proveedor no devolvió datos del VIX, se piden a IBKR")
            return await self.inner.get_vix_data(history_days, futures_ahead)
        return vix if vix.updated_at is not None else replace(vix, updated_at=self._now())

    def historical_request_counts(self) -> dict[str, int]:
        return self.inner.historical_request_counts()

    def pacing_wait_seconds(self) -> float:
        return self.inner.pacing_wait_seconds()

    # ---- subyacentes ------------------------------------------------------------------------
    async def get_underlying_price(self, ticker: str) -> Optional[float]:
        try:
            price = (await self.provider.get_prices([ticker])).get(ticker)
        except PriceError as exc:
            log.warning("Precio de %s no disponible en el proveedor, se pide a IBKR: %s", ticker, exc)
            return await self.inner.get_underlying_price(ticker)
        return price if price is not None else await self.inner.get_underlying_price(ticker)

    async def get_underlying_quotes(self, tickers: Sequence[str]) -> dict[str, UnderlyingQuote]:
        """Precio e IV a 30 días. Sin respuesta del proveedor para un ticker, se le pide a IBKR (solo a esos)."""
        names = list(tickers)
        try:
            prices = await self.provider.get_prices(names)
        except PriceError as exc:
            log.warning("Precios no disponibles en el proveedor, se piden a IBKR: %s", exc)
            return await self.inner.get_underlying_quotes(tickers)
        try:
            extras = await self.provider.get_market_extras(names)
        except VolatilityError as exc:
            log.warning("IV a 30 días no disponible en el proveedor: %s", exc)
            extras = {}
        out: dict[str, UnderlyingQuote] = {}
        for t in names:
            price = prices.get(t)
            iv = extras[t].iv30 if t in extras else None
            if price is not None or iv is not None:
                out[t] = UnderlyingQuote(price=price, iv=iv)
        missing = [t for t in names if t not in out]
        if missing:
            out.update(await self.inner.get_underlying_quotes(missing))
        return out

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        found = await self.get_days_to_ex_dividend_many([ticker])
        if ticker in found:
            return found[ticker]
        return await self.inner.get_days_to_ex_dividend(ticker)

    async def get_days_to_ex_dividend_many(self, tickers: Sequence[str]) -> dict[str, Optional[int]]:
        """Días hasta el ex-dividendo; None si la última fecha conocida ya pasó. Los que el proveedor no
        conoce, o si falla, se piden a IBKR."""
        names = list(tickers)
        try:
            extras = await self.provider.get_market_extras(names)
        except VolatilityError as exc:
            log.warning("Ex-dividendos no disponibles en el proveedor, se piden a IBKR: %s", exc)
            return await self.inner.get_days_to_ex_dividend_many(tickers)
        today = self._now().date()
        out: dict[str, Optional[int]] = {}
        for t in names:
            if t in extras:
                ex = extras[t].ex_dividend
                out[t] = (ex - today).days if ex is not None and ex >= today else None
        missing = [t for t in names if t not in out]
        if missing:
            out.update(await self.inner.get_days_to_ex_dividend_many(missing))
        return out

    # ---- cadena y contratos -----------------------------------------------------------------
    async def _listing(self, ticker: str) -> OptionListing:
        """Puts que existen de `ticker`, cacheados `listing_ttl_minutes`. Lanza OptionDataError si el proveedor falla."""
        cached = self._listings.get(ticker)
        if cached is not None and self._clock() - cached[0] < self.s.listing_ttl_minutes * 60:
            return cached[1]
        listing = await self.provider.get_put_listing(ticker)
        self._listings[ticker] = (self._clock(), listing)
        return listing

    async def get_option_chain(self, ticker: str) -> OptionChain:
        try:
            listing = await self._listing(ticker)
        except OptionDataError as exc:
            log.warning("Cadena de %s no disponible en el proveedor, se pide a IBKR: %s", ticker, exc)
            self._listings.pop(ticker, None)
            return await self.inner.get_option_chain(ticker)
        if not listing:
            raise DataUnavailableError(f"Sin cadena de opciones para {ticker}")
        return OptionChain(
            ticker, sorted({e for e, _ in listing}), sorted({k for _, k in listing}), MULTIPLIER,
        )

    async def qualify_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        """Solo conserva los contratos que existen en la lista real del proveedor (sin `con_id`: IBKR lo
        resuelve cuando hace falta, es decir, en el what-if de margen)."""
        def ident(c: OptionContract) -> tuple:
            return c.ticker, c.expiry, round(c.strike, 2), c.right

        by_ticker: dict[str, list[OptionContract]] = defaultdict(list)
        for c in contracts:
            by_ticker[c.ticker].append(c)
        valid: dict[tuple, OptionContract] = {}   # los validados por IBKR (fallback) traen su con_id
        for ticker, group in by_ticker.items():
            try:
                listing = await self._listing(ticker)
            except OptionDataError as exc:
                log.warning("Contratos de %s no verificables en el proveedor, se validan con IBKR: %s", ticker, exc)
                valid.update((ident(c), c) for c in await self.inner.qualify_contracts(group))
                continue
            valid.update((ident(c), c) for c in group if listing_key(c.expiry, c.strike) in listing)
        return [valid[k] for c in contracts if (k := ident(c)) in valid]

    async def get_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        symbols: dict[str, OptionContract] = {}
        for ticker in {c.ticker for c in contracts}:
            try:
                listing = await self._listing(ticker)
            except OptionDataError as exc:
                log.warning("Cotizaciones no disponibles en el proveedor, se piden a IBKR: %s", exc)
                return await self._inner_quotes(contracts)
            for c in contracts:
                if c.ticker == ticker and (sym := listing.get(listing_key(c.expiry, c.strike))):
                    symbols[sym] = c
        try:
            quotes = await self.provider.get_option_quotes(list(symbols))
        except OptionDataError as exc:
            log.warning("Cotizaciones de %d contratos no disponibles en el proveedor, se piden a IBKR: %s",
                        len(symbols), exc)
            return await self._inner_quotes(contracts)
        return {symbols[s]: q for s, q in quotes.items() if s in symbols}

    async def _inner_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        """Respaldo con IBKR, en lotes pequeños: TWS admite pocas líneas de mercado simultáneas."""
        out: dict[OptionContract, OptionQuote] = {}
        for i in range(0, len(contracts), self.fallback_batch):
            out.update(await self.inner.get_quotes(contracts[i:i + self.fallback_batch]))
        return out
