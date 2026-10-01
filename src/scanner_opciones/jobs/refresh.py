"""Refresco periódico de cotizaciones y métricas de contratos candidatos (RF-07)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OperationType
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, OptionQuote
from scanner_opciones.metrics.iv_stats import iv_percentile, iv_rank
from scanner_opciones.metrics.spread import spread_pct
from scanner_opciones.metrics.yields import annualized_yield_pct, gross_yield_pct
from scanner_opciones.metrics.yields import strike_distance_pct
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.storage.repositories import ContractRepo, IVHistoryRepo, SnapshotRepo, TickerInfoRepo
from typing import Callable, Optional, Sequence

log = logging.getLogger(__name__)


@dataclass
class RefreshReport:
    stored: int = 0        # contratos guardados en total
    in_scope: int = 0      # contratos que se cotizaron en este ciclo
    prices_updated: int = 0   # subyacentes con precio actualizado en este ciclo
    iv_updated: int = 0       # subyacentes con IV Rank / Percentile recalculados con la IV en directo
    refreshed: int = 0
    without_quote: int = 0
    margins_requested: int = 0
    quotes_kept: int = 0      # contratos sin precio nuevo (mercado cerrado): se conserva la última cotización
    margins_reused: int = 0   # márgenes recientes reutilizados sin pedir un what-if nuevo
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
        iv_history: Optional[IVHistoryRepo] = None,
    ) -> None:
        self.iv_history = iv_history
        self.gateway = gateway
        self.contracts = contracts
        self.snapshots = snapshots
        self.ticker_info = ticker_info
        self.settings = settings
        self.now = now

    def default_scope(self) -> list[ScanCriteria]:
        """Por defecto se cotizan solo los contratos que encajan con los valores iniciales de
        Regular o Táctica: el rango guardado (hasta -45 % / 60 DTE) es demasiado grande para
        cotizarlo entero cada pocos minutos. Para el resto, ver `run(criteria=...)`."""
        return [
            criteria_from_settings(self.settings, OperationType.REGULAR),
            criteria_from_settings(self.settings, OperationType.TACTICAL),
        ]

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

    async def run(self, criteria: Optional[Sequence[ScanCriteria]] = None) -> RefreshReport:
        """Cotiza los contratos guardados que encajan con `criteria` (por defecto, los valores
        iniciales de Regular y Táctica)."""
        report = RefreshReport()
        started = time.monotonic()
        criteria = list(criteria) if criteria else self.default_scope()
        infos = self.ticker_info.all()
        stored = self.contracts.list()
        today = self.now().date()
        infos = await self._refresh_underlyings(sorted({c.ticker for c in stored}), infos, report)
        t_underlyings = time.monotonic() - started
        all_contracts = [c for c in stored if self._in_scope(c, infos.get(c.ticker), criteria, today)]
        report.stored, report.in_scope = len(stored), len(all_contracts)
        # snapshots anteriores: de ellos se reutiliza el margen mientras sea reciente
        prev = {self._snap_key(s.contract): s for s in self.snapshots.all()} if all_contracts else {}
        size = self.settings.refresh.batch_size
        for i in range(0, len(all_contracts), size):
            batch = all_contracts[i : i + size]
            try:
                quotes = await self.gateway.get_quotes(batch)
            except BrokerDisconnectedError:
                raise
            except BrokerError as exc:
                for t in {c.ticker for c in batch}:
                    report.errors[t] = str(exc)
                log.warning("Cotizaciones fallidas: %s", exc)
                continue
            for contract in batch:
                quote = quotes.get(contract)
                if quote is None:
                    report.without_quote += 1
                    continue
                previous = prev.get(self._snap_key(contract))
                snap = self._build_snapshot(contract, quote, infos.get(contract.ticker), previous)
                if snap.updated_at != self.now():
                    report.quotes_kept += 1
                snap = await self._maybe_add_margin(snap, infos.get(contract.ticker), criteria, report, previous)
                if self.snapshots.upsert(snap):
                    report.refreshed += 1
        log.info(
            "Refresco: %d contratos cotizados (de %d guardados) en %.1f s (subyacentes %.1f s); "
            "márgenes pedidos %d, reutilizados %d; sin precio nuevo (se conserva el anterior): %d",
            report.in_scope, report.stored, time.monotonic() - started, t_underlyings,
            report.margins_requested, report.margins_reused, report.quotes_kept,
        )
        return report

    @staticmethod
    def _snap_key(c: OptionContract) -> tuple:
        return (c.ticker, c.expiry, c.strike, c.right)

    async def _refresh_underlyings(self, tickers: list[str], infos: dict, report: RefreshReport) -> dict:
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
        infos = dict(infos)
        window_start = self.now().date() - timedelta(days=self.settings.iv.lookback_days)
        for ticker, q in quotes.items():
            info = infos.get(ticker)
            if info is None:
                continue
            if q.price is not None:
                self.ticker_info.update_price(ticker, q.price)
                info = replace(info, underlying_price=q.price)
                report.prices_updated += 1
            if q.iv is not None and self.iv_history is not None:
                bars = self.iv_history.bars(ticker, since=window_start)
                values = [b[1] for b in bars]
                rank = iv_rank(q.iv, values, [b[2] for b in bars], [b[3] for b in bars])
                pct = iv_percentile(q.iv, values)
                if rank is not None or pct is not None:
                    self.ticker_info.update_iv_stats(ticker, rank, pct)
                    info = replace(info, iv_rank=rank, iv_percentile=pct)
                    report.iv_updated += 1
            infos[ticker] = info
        return infos

    def _build_snapshot(
        self, contract: OptionContract, q: OptionQuote, info, previous: Optional[ContractSnapshot] = None
    ) -> ContractSnapshot:
        """Si IBKR no devuelve ni bid ni ask (mercado cerrado, fallo puntual) NO se pisa la última
        cotización válida con vacíos: se conserva el bloque de precios (bid, ask, last, bid size) y
        `updated_at` sigue siendo el de esa cotización, para que se vea su antigüedad. Lo mismo con
        las griegas (delta, IV) y, campo a campo, con el open interest."""
        updated_at = self.now()
        bid, ask, last, bid_size = q.bid, q.ask, q.last, q.bid_size
        delta, iv, oi = q.delta, q.iv, q.open_interest
        if previous is not None:
            if bid is None and ask is None and (previous.bid is not None or previous.ask is not None):
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
        try:
            report.margins_requested += 1
            margin = await self.gateway.what_if_margin(snap.contract, 1)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            log.warning("what-if fallido para %s: %s", snap.contract, exc)
            return snap
        return replace(snap, initial_margin=margin, margin_at=self.now() if margin is not None else None)
