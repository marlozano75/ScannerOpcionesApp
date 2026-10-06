"""Actualización diaria por ticker (RF-04, RF-05, RF-06)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional, TypeVar

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError, VolatilityError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.prices import PriceProvider, reconcile_quotes
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.scanner.candidates import candidate_contracts
from scanner_opciones.storage.repositories import (
    ContractRepo, TickerInfoRepo, WatchlistRepo,
)

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class DailyUpdateReport:
    updated: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # ticker -> motivo


@dataclass
class _Prefetched:
    quotes: dict = field(default_factory=dict)    # ticker -> UnderlyingQuote
    ex_div: dict = field(default_factory=dict)    # ticker -> días hasta el ex-dividendo (o None)
    iv: dict = field(default_factory=dict)        # ticker -> IVMetrics de tastytrade


class DailyUpdater:
    def __init__(
        self,
        gateway: BrokerGateway,
        watchlist: WatchlistRepo,
        ticker_info: TickerInfoRepo,
        contracts: ContractRepo,
        settings: Settings,
        now: Callable[[], datetime] = datetime.now,
        volatility: Optional[VolatilityProvider] = None,
        prices: Optional[PriceProvider] = None,
    ) -> None:
        self.volatility = volatility
        self.prices = prices
        self.gateway = gateway
        self.watchlist = watchlist
        self.ticker_info = ticker_info
        self.contracts = contracts
        self.settings = settings
        self.now = now

    async def run_pending(self, on_progress=None) -> DailyUpdateReport:
        """Actualiza los tickers que no se han actualizado hoy (incluye los añadidos después)."""
        return await self.run(self.watchlist.pending_daily_update(self.now().date()), on_progress)

    async def run(
        self,
        tickers: list[str],
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        revalidate: bool = False,
    ) -> DailyUpdateReport:
        """`revalidate=True` olvida las combinaciones que IBKR no listaba y las vuelve a validar
        (por si IBKR ha listado strikes nuevos). Sin él solo se validan las combinaciones nuevas."""
        report = DailyUpdateReport()
        if not tickers:
            return report
        started = time.monotonic()
        counts_before = self.gateway.historical_request_counts()
        timings: dict[str, float] = defaultdict(float)  # segundos acumulados por paso (suma de tickers)
        shared = await self._prefetch(tickers, timings)
        sem = asyncio.Semaphore(self.settings.daily_update.concurrency)
        done = 0

        async def one(ticker: str) -> None:
            nonlocal done
            async with sem:
                try:
                    await self._update_ticker(ticker, shared, timings, revalidate)
                    report.updated.append(ticker)
                except BrokerDisconnectedError:
                    raise  # sin conexión no tiene sentido seguir con el resto
                except (BrokerError, ValueError) as exc:
                    log.warning("Actualización diaria fallida para %s: %s", ticker, exc)
                    report.errors[ticker] = str(exc)
                done += 1
                if on_progress:
                    on_progress(done, len(tickers), ticker)

        tasks = [asyncio.ensure_future(one(t)) for t in tickers]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        report.updated.sort(key=tickers.index)
        log.info(
            "Actualización diaria: %d tickers en %.1f s (suma por paso: %s)", len(tickers),
            time.monotonic() - started, ", ".join(f"{k} {v:.1f}s" for k, v in timings.items()),
        )
        counts_after = self.gateway.historical_request_counts()
        used = {k: v - counts_before.get(k, 0) for k, v in counts_after.items() if v > counts_before.get(k, 0)}
        if used:
            log.info("Peticiones históricas de la actualización diaria: %d (%s)", sum(used.values()),
                     ", ".join(f"{k} {v}" for k, v in sorted(used.items())))
        return report

    @staticmethod
    async def _timed(timings: dict[str, float], step: str, coro: Awaitable[T]) -> T:
        t0 = time.monotonic()
        try:
            return await coro
        finally:
            timings[step] += time.monotonic() - t0

    async def _prefetch(self, tickers: list[str], timings: dict[str, float]) -> _Prefetched:
        """Precio y dividendos de todos los tickers de una vez (una espera en lugar de una por ticker)."""
        out = _Prefetched()
        try:
            out.quotes = await self._timed(timings, "precios", self.gateway.get_underlying_quotes(tickers))
            out.quotes = await self._timed(
                timings, "precios externos",
                reconcile_quotes(self.prices, out.quotes, self.settings.tastytrade.price_max_deviation_pct),
            )
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            log.warning("Precios en lote no disponibles, se piden uno a uno: %s", exc)
        try:
            out.ex_div = await self._timed(
                timings, "dividendos", self.gateway.get_days_to_ex_dividend_many(tickers)
            )
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            log.warning("Dividendos en lote no disponibles, se piden uno a uno: %s", exc)
        if self.volatility is not None:  # una petición para todos los tickers
            try:
                out.iv = await self._timed(timings, "iv externo", self.volatility.get_iv_metrics(tickers))
            except VolatilityError as exc:
                log.warning("IV Rank/Percentile no disponibles, se conservan los guardados: %s", exc)
        return out

    async def _update_ticker(
        self, ticker: str, shared: _Prefetched, timings: dict[str, float], revalidate: bool = False
    ) -> None:
        now = self.now()
        today = now.date()
        gw = self.gateway

        def timed(step: str, coro: Awaitable[T]) -> Awaitable[T]:
            return self._timed(timings, step, coro)

        known = self.ticker_info.get(ticker)
        if known is not None and known.sector is not None:  # el sector casi no cambia: no se vuelve a pedir
            sector, category = known.sector, known.category
        else:
            sector, category = await timed("sector", gw.get_sector_info(ticker))
        quote = shared.quotes.get(ticker)
        price = quote.price if quote else None
        if price is None:
            price = await timed("precios", gw.get_underlying_price(ticker))
        price_at = now if price is not None else None
        if price is None and known is not None:
            price, price_at = known.underlying_price, known.price_at
        chain = await timed("cadena", gw.get_option_chain(ticker))
        if ticker in shared.ex_div:
            ex_div = shared.ex_div[ticker]
        else:
            ex_div = await timed("dividendos", gw.get_days_to_ex_dividend(ticker))

        # IV Rank / Percentile: los calcula tastytrade (una petición para todos, en `_prefetch`). Si no
        # los devuelve para este ticker (o falla), se conserva el último valor guardado.
        external = shared.iv.get(ticker)
        rank = external.iv_rank if external else (known.iv_rank if known else None)
        percentile = external.iv_percentile if external else (known.iv_percentile if known else None)

        if price:
            await self._sync_contracts(ticker, chain, price, today, revalidate, timings)

        self.ticker_info.upsert(
            TickerInfo(
                ticker=ticker, sector=sector, category=category, underlying_price=price,
                days_to_ex_dividend=ex_div,
                iv_rank=rank, iv_percentile=percentile,
                updated_daily_at=now, price_at=price_at,
            )
        )
        self.watchlist.mark_daily_updated(ticker, now)

    async def _sync_contracts(self, ticker, chain, price, today, revalidate, timings) -> None:
        """Catálogo incremental: solo se validan con IBKR las combinaciones que ni están guardadas
        ni se sabe que no existen. Los contratos que siguen en la ventana conservan su snapshot."""
        wanted = candidate_contracts(chain, price, today, self.settings.scanner.candidates)
        wanted_keys = {ContractRepo.key(c) for c in wanted}
        if revalidate:
            self.contracts.clear_misses(ticker)
        have = self.contracts.keys(ticker)
        missed = self.contracts.miss_keys(ticker)
        to_check = [c for c in wanted if (k := ContractRepo.key(c)) not in have and k not in missed]
        validated: list = []
        if to_check:  # descarta strikes que no existen para ese vencimiento y guarda el conId
            validated = await self._timed(timings, "validación", self.gateway.qualify_contracts(to_check))
            ok = {ContractRepo.key(c) for c in validated}
            self.contracts.add_misses(ticker, [c for c in to_check if ContractRepo.key(c) not in ok])
            log.info(
                "%s: %d de %d combinaciones nuevas existen en IBKR (las demás no están listadas; es normal)",
                ticker, len(validated), len(to_check),
            )
        removed, _ = self.contracts.sync_for_ticker(ticker, wanted_keys, validated)
        self.contracts.purge_expired_misses(today)
        if removed:
            log.info("%s: %d contratos retirados (vencidos o fuera de la ventana guardada)", ticker, removed)
