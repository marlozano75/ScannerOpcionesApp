"""Actualización diaria por ticker (RF-04, RF-05, RF-06)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Awaitable, Callable, Optional, TypeVar

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import (
    BrokerDisconnectedError, BrokerError, FinancialsError, UnsupportedTickerError, VolatilityError,
)
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.candles import CandleProvider
from scanner_opciones.jobs.contract_sync import ContractSyncer
from scanner_opciones.jobs.price_history import update_history
from scanner_opciones.marketdata.prices import PriceProvider, reconcile_quotes
from scanner_opciones.marketdata.financials import FinancialsProvider
from scanner_opciones.marketdata.fundamentals import FundamentalsProvider, resolve_eps
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.storage.repositories import (
    QualityRepo,
    BarRepo, ContractRepo, TickerInfoRepo, WatchlistRepo,
)

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class DailyUpdateReport:
    updated: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # ticker -> motivo
    unsupported: dict[str, str] = field(default_factory=dict)  # ticker -> motivo permanente: no sirve para esta app


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
        candles: Optional[CandleProvider] = None,
        bars: Optional[BarRepo] = None,
        syncer: Optional[ContractSyncer] = None,
        fundamentals: Optional[FundamentalsProvider] = None,
        financials: Optional[FinancialsProvider] = None,
    ) -> None:
        self.fundamentals = fundamentals
        self.financials = financials
        self.quality = QualityRepo(ticker_info.db)
        self.syncer = syncer or ContractSyncer(gateway, contracts, settings)
        self.volatility = volatility
        self.candles = candles
        self.bars = bars
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
                except UnsupportedTickerError as exc:   # permanente: lo quita de la watchlist quien llama
                    log.warning("Ticker no utilizable %s: %s", ticker, exc)
                    report.unsupported[ticker] = str(exc)
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

    def _quarters_stale(self, info: TickerInfo, today: date) -> bool:
        """¿Hay que bajar de nuevo el historial trimestral? Sí si no se bajó nunca, si pasó el plazo configurado
        o si desde entonces ha llegado la fecha de unos resultados (hay un trimestre nuevo)."""
        if info.fundamentals_at is None or info.positive_quarters is None:
            return True
        age = (today - info.fundamentals_at.date()).days
        if age >= self.settings.scanner.quality.refresh_days:
            return True
        return info.next_earnings is not None and info.fundamentals_at.date() <= info.next_earnings < today

    async def update_fundamentals(self, tickers: list[str]) -> int:
        """Datos de calidad (EPS, trimestres, capitalización, liquidez, resultados) de `tickers`. Los fundamentales
        salen de la petición de métricas (barata, siempre); el historial trimestral solo de los que lo tienen
        desactualizado. Es independiente de la actualización diaria «pendiente de hoy». Devuelve cuántas fichas
        actualizó. Un fallo del proveedor se registra y deja los datos como estaban."""
        if not tickers or self.fundamentals is None:
            return 0
        started = time.monotonic()
        infos = {t: self.quality.get(t) or TickerInfo(t) for t in tickers}
        try:
            funds = await self.fundamentals.get_fundamentals(list(infos))
        except VolatilityError as exc:
            log.warning("Datos de calidad no disponibles, se conservan los guardados: %s", exc)
            return 0
        today = self.now().date()
        stale = [t for t, i in infos.items() if self._quarters_stale(i, today)]
        quarters = await self.fundamentals.get_quarterly_eps(stale) if stale else {}
        updated = []
        for ticker, known in infos.items():
            fund, q = funds.get(ticker), quarters.get(ticker)
            if fund is None and q is None:
                continue
            fields: dict = {}
            if q is not None:
                fields.update(positive_quarters=sum(1 for x in q if x > 0), reported_quarters=len(q),
                              fundamentals_at=self.now())
            eps = resolve_eps(fund.eps_ttm if fund else None, q or [])
            if eps is not None:
                fields["eps_ttm"] = eps          # sin EPS válido ni historial nuevo se conserva el que había
            if fund is not None:
                fields.update(market_cap=fund.market_cap, option_liquidity=fund.option_liquidity,
                              next_earnings=fund.next_earnings, eps_surprise_pct=fund.eps_surprise_pct)
            updated.append(replace(known, **fields))
        n = self.quality.save(updated)
        log.info("Datos de calidad: %d fichas actualizadas (%d con historial trimestral nuevo) en %.1f s",
                 n, len(quarters), time.monotonic() - started)
        return n

    async def update_financials(self, tickers: list[str], batch: int = 50) -> int:
        """Apalancamiento y flujo de caja (SEC EDGAR) de los tickers cuyo dato tiene más de `edgar.refresh_days` días
        o no se ha consultado nunca. Se guarda por lotes: si se interrumpe (o la SEC bloquea) se conserva lo hecho.
        Los tickers que EDGAR no conoce (emisores extranjeros) se anotan igualmente, para no insistir cada arranque.
        Devuelve cuántas fichas actualizó."""
        if not tickers or self.financials is None:
            return 0
        today = self.now().date()
        max_age = self.settings.edgar.refresh_days
        stale = [t for t in tickers if (i := self.quality.get(t)) is None or i.financials_at is None
                 or (today - i.financials_at.date()).days >= max_age]
        if not stale:
            return 0
        started, done = time.monotonic(), 0
        for k in range(0, len(stale), batch):
            chunk = stale[k:k + batch]
            try:
                found = await self.financials.get_financials(chunk)
            except FinancialsError as exc:
                log.warning("Balance y flujo de caja no disponibles (SEC EDGAR), se conservan los guardados: %s", exc)
                break
            updated = []
            for ticker in chunk:
                fin = found.get(ticker)
                if fin is None:
                    continue    # fallo puntual de ese ticker: se reintenta en la próxima pasada
                updated.append(replace(
                    self.quality.get(ticker) or TickerInfo(ticker), liabilities_to_equity=fin.liabilities_to_equity, fcf_ttm=fin.fcf_ttm,
                    financials_end=fin.period_end, financials_at=self.now(),
                    debt_to_equity=fin.debt_to_equity,
                    interest_coverage=fin.interest_coverage,
                    cash_to_short_debt=fin.cash_to_short_debt,
                    ocf_to_debt=fin.ocf_to_debt,
                    capex_to_ocf=fin.capex_to_ocf,
                    fcf_to_assets=fin.fcf_to_assets,
                    net_buyback_pct=fin.net_buyback_pct,
                    roic=fin.roic, loss_years=fin.loss_years, fiscal_years=fin.fiscal_years,
                    revenue_drop_years=fin.revenue_drop_years, revenue_years=fin.revenue_years,
                    earnings_volatility=fin.earnings_volatility))
            done += self.quality.save(updated)
        log.info("Balance y flujo de caja (SEC EDGAR): %d de %d fichas actualizadas en %.1f s", done, len(stale),
                 time.monotonic() - started)
        return done

    async def update_history(self, tickers: list[str]) -> int:
        """Completa el histórico de cierres diarios de `tickers` (solo los días que faltan).
        Es independiente de la actualización diaria «pendiente de hoy»: se hace siempre que falte histórico.
        Devuelve cuántos tickers tienen histórico guardado."""
        if not tickers or self.candles is None or self.bars is None:
            return 0
        started = time.monotonic()
        await update_history(self.candles, self.bars, tickers, self.now().date(), self.settings.trend)
        have = self.bars.last_days(tickers)
        missing = [t for t in tickers if t not in have]
        if missing:
            log.info("Sin cierres de tastytrade para: %s", ", ".join(missing))
        log.info("Histórico de cierres: %d tickers en %.1f s", len(tickers), time.monotonic() - started)
        return len(have)

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
        self.syncer.remember_chain(ticker, chain, today)
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
            await self.syncer.sync(ticker, chain, price, today, revalidate, timings)

        self.ticker_info.upsert(
            TickerInfo(
                ticker=ticker, sector=sector, category=category, underlying_price=price,
                days_to_ex_dividend=ex_div,
                iv_rank=rank, iv_percentile=percentile,
                updated_daily_at=now, price_at=price_at,
            )
        )
        self.watchlist.mark_daily_updated(ticker, now)
