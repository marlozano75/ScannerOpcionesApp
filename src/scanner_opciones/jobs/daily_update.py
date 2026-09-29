"""Actualización diaria por ticker (RF-04, RF-05, RF-06)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.iv_stats import iv_percentile, iv_rank
from scanner_opciones.scanner.candidates import candidate_contracts
from scanner_opciones.storage.repositories import (
    ContractRepo, IVHistoryRepo, TickerInfoRepo, WatchlistRepo,
)

log = logging.getLogger(__name__)


@dataclass
class DailyUpdateReport:
    updated: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # ticker -> motivo


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
    ) -> None:
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
        self, tickers: list[str], on_progress: Optional[Callable[[int, int, str], None]] = None
    ) -> DailyUpdateReport:
        report = DailyUpdateReport()
        for i, ticker in enumerate(tickers, start=1):
            if on_progress:
                on_progress(i, len(tickers), ticker)
            try:
                await self._update_ticker(ticker)
                report.updated.append(ticker)
            except BrokerDisconnectedError:
                raise  # sin conexión no tiene sentido seguir con el resto
            except (BrokerError, ValueError) as exc:
                log.warning("Actualización diaria fallida para %s: %s", ticker, exc)
                report.errors[ticker] = str(exc)
        return report

    async def _update_ticker(self, ticker: str) -> None:
        now = self.now()
        today = now.date()
        gw = self.gateway

        sector, category = await gw.get_sector_info(ticker)
        price = await gw.get_underlying_price(ticker)
        chain = await gw.get_option_chain(ticker)
        ex_div = await gw.get_days_to_ex_dividend(ticker)

        # IV incremental: solo los días posteriores al último guardado (RF-06)
        last = self.iv_history.last_day(ticker)
        new_points = await gw.get_iv_history(ticker, last)
        if new_points:
            self.iv_history.add(ticker, new_points)
        window_start = today - timedelta(days=self.settings.iv.lookback_days)
        self.iv_history.prune(ticker, window_start)
        series = self.iv_history.series(ticker, since=window_start)
        values = [v for _, v in series]
        current_iv = values[-1] if values else None

        candidates = (
            candidate_contracts(chain, price, today, self.settings.scanner.candidates) if price else []
        )
        if candidates:  # descarta strikes que no existen para ese vencimiento y guarda el conId
            candidates = await gw.qualify_contracts(candidates)

        self.contracts.replace_for_ticker(ticker, candidates)
        self.ticker_info.upsert(
            TickerInfo(
                ticker=ticker, sector=sector, category=category, underlying_price=price,
                days_to_ex_dividend=ex_div,
                iv_rank=iv_rank(current_iv, values),
                iv_percentile=iv_percentile(current_iv, values),
                updated_daily_at=now,
            )
        )
        self.watchlist.mark_daily_updated(ticker, now)
