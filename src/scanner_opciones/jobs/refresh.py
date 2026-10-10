"""Refresco periódico de cotizaciones y métricas de contratos candidatos (RF-07)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError, VolatilityError
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, OptionQuote
from scanner_opciones.jobs.contract_sync import ContractSyncer
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.marketdata.prices import PriceProvider, reconcile_quotes
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.metrics.spread import spread_pct
from scanner_opciones.metrics.yields import annualized_yield_pct, gross_yield_pct
from scanner_opciones.metrics.yields import strike_distance_pct
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.storage.repositories import ContractRepo, SnapshotRepo, TickerInfoRepo
from typing import Callable, Optional, Sequence

log = logging.getLogger(__name__)

MARGIN_MAX_FAILURES = 3   # what-if seguidos sin respuesta tras los que se deja de pedir márgenes en el ciclo


@dataclass
class RefreshReport:
    stored: int = 0        # contratos guardados en total
    in_scope: int = 0      # contratos que se cotizaron en este ciclo
    prices_updated: int = 0   # subyacentes con precio actualizado en este ciclo
    iv_updated: int = 0       # subyacentes con IV Rank / Percentile recalculados con la IV en directo
    contracts_added: int = 0   # contratos nuevos guardados porque el precio movió la ventana
    contracts_removed: int = 0  # contratos retirados por salir de la ventana guardada
    refreshed: int = 0
    without_quote: int = 0
    margins_requested: int = 0
    quotes_kept: int = 0      # contratos sin precio nuevo (mercado cerrado): se conserva la última cotización
    margins_reused: int = 0   # márgenes recientes reutilizados sin pedir un what-if nuevo
    margin_streak: int = 0    # what-if fallidos seguidos; al llegar a MARGIN_MAX_FAILURES no se piden más en el ciclo
    errors: dict[str, str] = field(default_factory=dict)  # ticker -> motivo


class RefreshJob:
    def __init__(
        self,
        gateway: BrokerGateway,
        contracts: ContractRepo,
        snapshots: SnapshotRepo,
        ticker_info: TickerInfoRepo,
        settings: Settings,
        now: Callable[[], datetime] = datetime.now,
        volatility: Optional[VolatilityProvider] = None,
        prices: Optional[PriceProvider] = None,
        syncer: Optional[ContractSyncer] = None,
        market: Optional[MarketCalendar] = None,
    ) -> None:
        self.market = market
        self.syncer = syncer or ContractSyncer(gateway, contracts, settings)
        self.volatility = volatility
        self.prices = prices
        self.gateway = gateway
        self.contracts = contracts
        self.snapshots = snapshots
        self.ticker_info = ticker_info
        self.settings = settings
        self.now = now

    def default_scope(self) -> list[ScanCriteria]:
        """Por defecto se cotizan solo los contratos que encajan con los valores iniciales del
        filtro del scanner: el rango guardado (hasta -40 % / 45 DTE) es demasiado grande para
        cotizarlo entero cada pocos minutos. Para el resto, ver `run(criteria=...)`."""
        return [criteria_from_settings(self.settings)]

    @staticmethod
    def _in_scope(contract: OptionContract, info, criteria: Sequence[ScanCriteria], today) -> bool:
        price = info.underlying_price if info else None
        dist = strike_distance_pct(price, contract.strike)
        if dist is None:
            return False
        dte = contract.dte(today)
        return any(
            c.dte_min <= dte <= c.dte_max and c.strike_below_pct_min - 1e-9 <= dist <= c.strike_below_pct_max + 1e-9
            for c in criteria
        )

    async def run(
        self, criteria: Optional[Sequence[ScanCriteria]] = None, only_unquoted: bool = False
    ) -> RefreshReport:
        """Cotiza los contratos guardados que encajan con `criteria` (por defecto, los valores
        iniciales del filtro del scanner). `only_unquoted=True`: solo los que nunca se han cotizado
        (contratos nuevos), sin volver a pedir precio/IV de los subyacentes."""
        report = RefreshReport()
        started = time.monotonic()
        criteria = list(criteria) if criteria else self.default_scope()
        infos = self.ticker_info.all()
        stored = self.contracts.list()
        today = self.now().date()
        if not only_unquoted:
            moved: set[str] = set()
            infos = await self._refresh_underlyings(sorted({c.ticker for c in stored}), infos, report, moved)
            if moved:  # el precio nuevo desplaza la ventana guardada: se completan los contratos que falten
                await self._sync_catalog(sorted(moved), infos, today, report)
                stored = self.contracts.list()
        t_underlyings = time.monotonic() - started
        all_contracts = [c for c in stored if self._in_scope(c, infos.get(c.ticker), criteria, today)]
        # snapshots anteriores: de ellos se reutiliza el margen mientras sea reciente
        prev = {self._snap_key(s.contract): s for s in self.snapshots.all()} if all_contracts else {}
        if only_unquoted:
            all_contracts = [c for c in all_contracts if self._snap_key(c) not in prev]
        report.stored, report.in_scope = len(stored), len(all_contracts)
        # con tastytrade el gateway fija un lote grande (una conexión DXLink admite miles de suscripciones)
        size = getattr(self.gateway, "quote_batch_size", None) or self.settings.refresh.batch_size
        batches = [all_contracts[i:i + size] for i in range(0, len(all_contracts), size)]

        async def fetch(batch: list):
            try:
                return await self.gateway.get_quotes(batch)
            except BrokerError as exc:   # incluye BrokerDisconnectedError: se trata al consumir el resultado
                return exc

        # El siguiente lote se pide mientras se guardan los snapshots y se piden los márgenes (IBKR) del actual.
        pending = asyncio.ensure_future(fetch(batches[0])) if batches else None
        try:
            for index, batch in enumerate(batches):
                quotes = await pending
                pending = asyncio.ensure_future(fetch(batches[index + 1])) if index + 1 < len(batches) else None
                if isinstance(quotes, BrokerDisconnectedError):
                    raise quotes
                if isinstance(quotes, BrokerError):
                    for t in {c.ticker for c in batch}:
                        report.errors[t] = str(quotes)
                    log.warning("Cotizaciones fallidas: %s", quotes)
                    continue
                ready: list[ContractSnapshot] = []
                try:
                    for contract in batch:
                        quote = quotes.get(contract)
                        if quote is None:
                            report.without_quote += 1
                            continue
                        previous = prev.get(self._snap_key(contract))
                        snap = self._build_snapshot(contract, quote, infos.get(contract.ticker), previous)
                        if snap.updated_at != self.now():
                            report.quotes_kept += 1
                        ready.append(await self._maybe_add_margin(
                            snap, infos.get(contract.ticker), criteria, report, previous))
                finally:   # una sola transacción por lote; si se pierde la conexión a mitad, se guarda lo ya hecho
                    report.refreshed += self.snapshots.upsert_many(ready)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
        log.info(
            "Refresco: %d contratos cotizados (de %d guardados) en %.1f s (subyacentes %.1f s); "
            "márgenes pedidos %d, reutilizados %d; sin precio nuevo (se conserva el anterior): %d; "
            "catálogo: +%d / -%d contratos",
            report.in_scope, report.stored, time.monotonic() - started, t_underlyings,
            report.margins_requested, report.margins_reused, report.quotes_kept,
            report.contracts_added, report.contracts_removed,
        )
        return report

    @staticmethod
    def _snap_key(c: OptionContract) -> tuple:
        return (c.ticker, c.expiry, c.strike, c.right)

    async def _sync_catalog(self, tickers: list[str], infos: dict, today, report: RefreshReport) -> None:
        """Recalcula la ventana guardada de cada ticker con su precio actual: valida y guarda solo los
        contratos que faltan y retira los que quedan fuera del margen. Un fallo no interrumpe el refresco."""
        sem = asyncio.Semaphore(self.settings.daily_update.concurrency)

        async def one(ticker: str) -> None:
            price = infos[ticker].underlying_price
            if not price:
                return
            async with sem:
                try:
                    chain = await self.syncer.chain(ticker, today)
                    removed, added = await self.syncer.sync(ticker, chain, price, today)
                except BrokerDisconnectedError:
                    raise
                except BrokerError as exc:
                    report.errors[ticker] = str(exc)
                    log.warning("Catálogo de contratos no actualizado para %s: %s", ticker, exc)
                    return
            report.contracts_added += added
            report.contracts_removed += removed

        tasks = [asyncio.ensure_future(one(t)) for t in tickers]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _refresh_underlyings(
        self, tickers: list[str], infos: dict, report: RefreshReport, moved: Optional[set] = None
    ) -> dict:
        """Actualiza precio e IV en directo de cada subyacente y recalcula IV Rank / Percentile con
        la IV actual (no con la última barra diaria guardada). Se hace antes de calcular alcance."""
        if not tickers:
            return infos
        try:
            quotes = await self.gateway.get_underlying_quotes(tickers)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            log.warning("No se pudieron actualizar precio/IV de los subyacentes: %s", exc)
            return infos
        quotes = await reconcile_quotes(self.prices, quotes, self.settings.tastytrade.price_max_deviation_pct)
        infos = dict(infos)
        external: dict = {}
        if self.volatility is not None:  # una petición para todos los tickers
            try:
                external = await self.volatility.get_iv_metrics(tickers)
            except VolatilityError as exc:
                log.warning("IV Rank/Percentile no disponibles, se conservan los guardados: %s", exc)
        for ticker, q in quotes.items():
            info = infos.get(ticker)
            if info is None:
                continue
            if q.price is not None:
                now = self.now()
                self.ticker_info.update_price(ticker, q.price, now)
                info = replace(info, underlying_price=q.price, price_at=now)
                report.prices_updated += 1
                if moved is not None:
                    moved.add(ticker)
            if (metrics := external.get(ticker)) is not None:
                self.ticker_info.update_iv_stats(ticker, metrics.iv_rank, metrics.iv_percentile)
                info = replace(info, iv_rank=metrics.iv_rank, iv_percentile=metrics.iv_percentile)
                report.iv_updated += 1
            infos[ticker] = info
        return infos

    def _build_snapshot(
        self, contract: OptionContract, q: OptionQuote, info, previous: Optional[ContractSnapshot] = None
    ) -> ContractSnapshot:
        """Si IBKR no devuelve ni bid ni ask (mercado cerrado, fallo puntual) NO se pisa la última
        cotización válida con vacíos (ni, con el mercado cerrado, con un bid de 0 y un ask inflado): se conserva el bloque de precios (bid, ask, last, bid size) y
        `updated_at` sigue siendo el de esa cotización, para que se vea su antigüedad. Lo mismo con
        las griegas (delta, IV) y, campo a campo, con el open interest."""
        updated_at = self.now()
        bid, ask, last, bid_size = q.bid, q.ask, q.last, q.bid_size
        delta, iv, oi = q.delta, q.iv, q.open_interest
        if previous is not None:
            if bid is None and ask is None and (previous.bid is not None or previous.ask is not None):
                bid, ask, last, bid_size = previous.bid, previous.ask, previous.last, previous.bid_size
                updated_at = previous.updated_at or updated_at
            elif (bid == 0 and ask is not None and ask > 0 and previous.bid is not None and previous.bid > 0
                  and self.market is not None and not self.market.is_open(self.now())):
                # fuera de horario tastytrade devuelve bid 0 con un ask inflado: no pisa la cotización de la sesión
                bid, ask, last, bid_size = previous.bid, previous.ask, previous.last, previous.bid_size
                updated_at = previous.updated_at or updated_at
            if delta is None and iv is None:
                delta, iv = previous.delta, previous.iv
            if oi is None:
                oi = previous.open_interest
        y = gross_yield_pct(bid, ask, contract.strike)
        dte = contract.dte(self.now().date())
        return ContractSnapshot(
            contract=contract, updated_at=updated_at,
            bid=bid, ask=ask, last=last, delta=delta, iv=iv, open_interest=oi,
            bid_size=bid_size,
            spread_pct=spread_pct(bid, ask), yield_pct=y,
            yield_annualized_pct=annualized_yield_pct(y, dte),
            iv_rank=info.iv_rank if info else None,
            iv_percentile=info.iv_percentile if info else None,
        )

    async def _maybe_add_margin(
        self, snap, info, criteria, report: RefreshReport, previous: Optional[ContractSnapshot] = None
    ) -> ContractSnapshot:
        """Solo pide what-if para contratos que pasan algún escaneo (limita las peticiones) y
        reutiliza el margen del snapshot anterior si es más reciente que `margin_max_age_minutes`."""
        price = info.underlying_price if info else None
        today = self.now().date()
        passes = any(
            reject_reason(snap, price, today, c, snap.iv_rank, snap.iv_percentile) is None
            for c in criteria
        )
        if not passes:
            return snap
        max_age = timedelta(minutes=self.settings.refresh.margin_max_age_minutes)
        if (
            previous is not None and previous.initial_margin is not None and previous.margin_at is not None
            and self.now() - previous.margin_at < max_age
        ):
            report.margins_reused += 1
            return replace(snap, initial_margin=previous.initial_margin, margin_at=previous.margin_at)
        # Sin respuesta de TWS se conserva el último margen conocido (con su fecha: se volverá a pedir en cuanto
        # responda) en lugar de borrarlo: un margen antiguo es mejor que ninguno.
        stale = snap
        if previous is not None and previous.initial_margin is not None:
            stale = replace(snap, initial_margin=previous.initial_margin, margin_at=previous.margin_at)
        if report.margin_streak >= MARGIN_MAX_FAILURES:
            return stale   # TWS no responde: no se espera un tiempo máximo por cada contrato
        try:
            report.margins_requested += 1
            margin = await self.gateway.what_if_margin(snap.contract, 1)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            report.margin_streak += 1
            log.warning("what-if fallido para %s: %s", snap.contract, exc)
            if report.margin_streak == MARGIN_MAX_FAILURES:
                log.warning("%d what-if seguidos fallidos: no se piden más márgenes en este ciclo", MARGIN_MAX_FAILURES)
            return stale
        report.margin_streak = 0
        return replace(snap, initial_margin=margin, margin_at=self.now() if margin is not None else None)
