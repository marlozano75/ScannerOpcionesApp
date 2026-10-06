"""Actualización diaria por ticker (RF-04, RF-05, RF-06)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional, TypeVar

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError, VolatilityError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.metrics.iv_stats import iv_percentile, iv_rank
from scanner_opciones.scanner.candidates import candidate_contracts
from scanner_opciones.storage.repositories import (
    ContractRepo, IVHistoryRepo, TickerInfoRepo, WatchlistRepo,
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
    iv: dict = field(default_factory=dict)        # ticker -> IVMetrics del proveedor externo (si lo hay)


class DailyUpdater:
    def __init__(
        self,
        gateway: BrokerGateway,
        watchlist: WatchlistRepo,
        ticker_info: TickerInfoRepo,
        iv_history: IVHistoryRepo,
        contracts: ContractRepo,
        settings: Settings,
        now: Callable[[], datetime] = datetime.now,
        volatility: Optional[VolatilityProvider] = None,
    ) -> None:
        self.volatility = volatility
        self.gateway = gateway
        self.watchlist = watchlist
        self.ticker_info = ticker_info
        self.iv_history = iv_history
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
        if self.volatility is not None:  # una petición para todos; los que no cubra caen al cálculo con IBKR
            try:
                out.iv = await self._timed(timings, "iv externo", self.volatility.get_iv_metrics(tickers))
            except VolatilityError as exc:
                log.warning("IV Rank/Percentile externos no disponibles, se calculan con IBKR: %s", exc)
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
        if price is None and known is not None:
            price = known.underlying_price
        chain = await timed("cadena", gw.get_option_chain(ticker))
        if ticker in shared.ex_div:
            ex_div = shared.ex_div[ticker]
        else:
            ex_div = await timed("dividendos", gw.get_days_to_ex_dividend(ticker))

        # IV incremental: solo desde el último día guardado (RF-06); ese último día se rehace porque
        # su barra podía ser parcial. Si hay barras sin máximo/mínimo (guardadas antes de la
        # migración v3) se descarga la ventana completa una vez.
        external = shared.iv.get(ticker)
        if external is not None:  # el proveedor ya los calcula: no se descarga el historial de IV
            rank, percentile = external.iv_rank, external.iv_percentile
        else:
            window_start = today - timedelta(days=self.settings.iv.lookback_days)
            last = self.iv_history.last_day(ticker)
            if last is not None and self.iv_history.needs_hilo_backfill(ticker, window_start):
                last = None
            new_points = await timed("iv", gw.get_iv_history(ticker, last))
            if new_points:
                self.iv_history.add(ticker, new_points)
            self.iv_history.prune(ticker, window_start)
            bars = self.iv_history.bars(ticker, since=window_start)
            values = [b[1] for b in bars]
            current_iv = values[-1] if values else None
            rank = iv_rank(current_iv, values, [b[2] for b in bars], [b[3] for b in bars])
            percentile = iv_percentile(current_iv, values)

        if price:
            await self._sync_contracts(ticker, chain, price, today, revalidate, timings)

        self.ticker_info.upsert(
            TickerInfo(
                ticker=ticker, sector=sector, category=category, underlying_price=price,
                days_to_ex_dividend=ex_div,
                iv_rank=rank, iv_percentile=percentile,
                updated_daily_at=now,
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
