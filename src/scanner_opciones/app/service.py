"""Servicio de aplicación: orquesta jobs, estado en memoria y casos de uso para la UI."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OperationType
from scanner_opciones.domain.errors import BrokerDisconnectedError, BrokerError
from scanner_opciones.domain.models import (
    AccountSummary, OptionContract, Position, RiskStatus, SectorExposure, VixData,
)
from scanner_opciones.jobs.daily_update import DailyUpdater, DailyUpdateReport
from scanner_opciones.jobs.refresh import RefreshJob, RefreshReport
from scanner_opciones.portfolio.cushion import build_risk_status
from scanner_opciones.portfolio.leverage import AssignmentExposure, assignment_exposure
from scanner_opciones.portfolio.diversification import WeekExposure, sector_exposure, weekly_sector_exposure
from scanner_opciones.portfolio.simulator import SimulatedTrade, SimulationResult, simulate
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.engine import ScanOutput, run_scan
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    ContractRepo, IVHistoryRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)
from scanner_opciones.watchlist.parser import ParseResult

log = logging.getLogger(__name__)

STEP_TIMEOUT_SECONDS = 90  # un paso de red colgado no debe bloquear el refresco para siempre


@dataclass
class SelectedContract:
    ticker: str
    expiry: date
    strike: float
    quantity: int = 1


@dataclass
class AppState:
    connected: bool = False
    account: Optional[AccountSummary] = None
    positions: list[Position] = field(default_factory=list)
    risk: Optional[RiskStatus] = None
    vix: Optional[VixData] = None
    last_refresh: Optional[datetime] = None
    last_daily_report: Optional[DailyUpdateReport] = None
    last_refresh_report: Optional[RefreshReport] = None
    errors: dict[str, str] = field(default_factory=dict)  # área -> último error
    activity: Optional[str] = None   # tarea en curso (se muestra en la interfaz)


class AppService:
    def __init__(
        self,
        gateway: BrokerGateway,
        db: Database,
        settings: Settings,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.now = now
        self.watchlist = WatchlistRepo(db)
        self.ticker_info = TickerInfoRepo(db)
        self.iv_history = IVHistoryRepo(db)
        self.contracts = ContractRepo(db)
        self.snapshots = SnapshotRepo(db)
        self.daily = DailyUpdater(gateway, self.watchlist, self.ticker_info, self.iv_history,
                                  self.contracts, settings, now)
        self.refresh_job = RefreshJob(gateway, self.contracts, self.snapshots, self.ticker_info, settings, now)
        self.state = AppState()
        self._lock = asyncio.Lock()  # evita ejecuciones solapadas
        self._background: set = set()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ---- ciclo de vida ---------------------------------------------------------------------
    async def start(self) -> None:
        try:
            await self.gateway.connect()
        except BrokerError as exc:
            self.state.connected = False
            self.state.errors["connection"] = str(exc)
            return
        self.state.connected = True
        self.state.errors.pop("connection", None)
        self.cleanup_orphans()
        if self.settings.daily_update.run_on_startup:
            await self.run_daily()
        await self.refresh_all()

    async def stop(self) -> None:
        await self.gateway.disconnect()
        self.state.connected = False

    async def switch_gateway(self, new_gateway: BrokerGateway) -> None:
        """Cambio real <-> simulada (RF-01): cierra la conexión, descarta el estado de cuenta
        en memoria (no se mezclan datos de cuentas) y reconecta con el nuevo broker."""
        async with self._lock:
            await self.gateway.disconnect()
            self.gateway = new_gateway
            self.daily.gateway = new_gateway
            self.refresh_job.gateway = new_gateway
            self.state = AppState()
        await self.start()

    # ---- watchlist -------------------------------------------------------------------------
    async def add_watchlist(self, parsed: ParseResult) -> list[str]:
        """Añade tickers y lanza la actualización diaria de los nuevos (RF-05)."""
        new = self.watchlist.add(parsed.tickers, self.now())
        if new and self.state.connected:
            await self.run_daily(new)
        return new

    def remove_ticker(self, ticker: str) -> dict[str, int]:
        """Quita el ticker de la watchlist y borra sus contratos (con sus cotizaciones) y su ficha.
        Se conserva el historial de IV: ahorra descargarlo si se vuelve a añadir."""
        self.watchlist.remove(ticker)
        had_info = self.ticker_info.get(ticker) is not None
        self.ticker_info.delete(ticker)
        return {"contracts": self.contracts.delete_for_ticker(ticker), "ticker_info": int(had_info)}

    def cleanup_orphans(self) -> dict[str, int]:
        """Borra contratos y fichas de tickers que ya no están en la watchlist (p. ej. quitados
        con una versión anterior). Barato: se llama al arrancar y antes de cada refresco."""
        keep = self.watchlist.list()
        removed = {
            "contracts": self.contracts.purge_except(keep),
            "ticker_info": self.ticker_info.purge_except(keep),
        }
        if any(removed.values()):
            log.info("Limpieza de tickers fuera de la watchlist: %s", removed)
        return removed

    def launch(self, coro) -> None:
        """Ejecuta una corrutina en segundo plano (acciones manuales largas)."""
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def wait_idle(self) -> None:
        """Espera a que terminen las tareas lanzadas con `launch` (útil en tests)."""
        while True:
            pending = [t for t in self._background if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    # ---- jobs ------------------------------------------------------------------------------
    def _progress(self, i: int, total: int, ticker: str) -> None:
        self.state.activity = f"Actualización diaria: {ticker} ({i}/{total})"

    async def run_daily(
        self, tickers: Optional[list[str]] = None, wait: bool = False
    ) -> Optional[DailyUpdateReport]:
        """`wait=False`: se omite si hay otra tarea en curso (uso automático).
        `wait=True`: espera su turno (acciones manuales: no se pierden en silencio)."""
        if self.busy and not wait:
            log.info("Actualización diaria omitida: hay otra ejecución en curso")
            return None
        if self.busy:
            self.state.activity = self.state.activity or "En cola: actualización diaria"
        async with self._lock:
            try:
                self.cleanup_orphans()
                if tickers is not None:
                    report = await self.daily.run(tickers, self._progress)
                else:
                    report = await self.daily.run_pending(self._progress)
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return None
            finally:
                self.state.activity = None
            self.state.last_daily_report = report
            return report

    async def refresh_all(self) -> bool:
        """Cartera, riesgo, VIX y contratos. False si se omitió por solapamiento o falta de conexión."""
        if self.busy:
            log.info("Refresco omitido: hay otra ejecución en curso")
            return False
        async with self._lock:
            try:
                self.state.activity = "Refrescando cartera, VIX y cotizaciones"
                self.cleanup_orphans()
                await self._refresh_portfolio()
                await self._refresh_vix()
                self.state.last_refresh_report = await self.refresh_job.run()
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            finally:
                self.state.activity = None
            self.state.last_refresh = self.now()
            return True

    async def refresh_scoped(self, criteria: ScanCriteria) -> Optional[RefreshReport]:
        """Cotiza los contratos que encajan con `criteria` (p. ej. un rango distinto del inicial)."""
        if self.busy:
            log.info("Refresco omitido: hay otra ejecución en curso")
            return None
        async with self._lock:
            try:
                report = await self.refresh_job.run([criteria])
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return None
            self.state.last_refresh_report = report
            self.state.last_refresh = self.now()
            return report

    def _disconnected(self, exc: Exception) -> None:
        self.state.connected = False
        self.state.errors["connection"] = str(exc)
        log.warning("Conexión perdida: %s", exc)

    async def _refresh_portfolio(self) -> None:
        try:
            self.state.account = await self.gateway.get_account_summary()
            self.state.positions = await self.gateway.get_positions()
            self.state.risk = build_risk_status(self.state.account, self.settings.risk.cushion_thresholds)
            self.state.errors.pop("portfolio", None)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            self.state.errors["portfolio"] = str(exc)

    async def _refresh_vix(self) -> None:
        try:
            self.state.vix = await asyncio.wait_for(
                self.gateway.get_vix_data(self.settings.vix.history_days, self.settings.vix.futures_ahead),
                timeout=STEP_TIMEOUT_SECONDS,
            )
            self.state.errors.pop("vix", None)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            self.state.errors["vix"] = str(exc)
        except asyncio.TimeoutError:
            self.state.errors["vix"] = "Timeout obteniendo el VIX"

    # ---- casos de uso de lectura -------------------------------------------------------------
    def criteria(self, operation: OperationType, **overrides) -> ScanCriteria:
        return criteria_from_settings(self.settings, operation).with_filters(**overrides)

    def scan(self, criteria: ScanCriteria, include_rejections: bool = False) -> ScanOutput:
        return run_scan(
            self.snapshots.all(), self.ticker_info.all(), self.state.positions, criteria,
            self.now().date(), include_rejections,
        )

    def assignment(self) -> AssignmentExposure:
        return assignment_exposure(self.state.positions, self.state.account)

    def diversification(self) -> tuple[SectorExposure, list[WeekExposure]]:
        pos = self.state.positions
        return (
            sector_exposure(pos),
            weekly_sector_exposure(pos, self.now().date(), self.settings.diversification.weeks_ahead),
        )

    async def simulate(self, selected: list[SelectedContract]) -> SimulationResult:
        """Simula vender y ser asignado en los contratos elegidos. Margen aproximado (suma)."""
        infos = self.ticker_info.all()
        snaps = {(s.contract.ticker, s.contract.expiry, s.contract.strike): s for s in self.snapshots.all()}
        trades: list[SimulatedTrade] = []
        for sel in selected:
            snap = snaps.get((sel.ticker, sel.expiry, sel.strike))
            if snap is None:
                raise ValueError(f"Contrato desconocido: {sel.ticker} {sel.expiry} {sel.strike}")
            margin = snap.initial_margin * sel.quantity if snap.initial_margin is not None else None
            if margin is None and self.state.connected:
                try:
                    margin = await self.gateway.what_if_margin(snap.contract, sel.quantity)
                except BrokerError:
                    margin = None
            info = infos.get(sel.ticker)
            trades.append(SimulatedTrade(snap.contract, sel.quantity, info.sector if info else None, margin))
        return simulate(
            self.state.positions, trades, self.state.account, self.settings.risk.cushion_thresholds,
            self.now().date(), self.settings.diversification.weeks_ahead,
        )
