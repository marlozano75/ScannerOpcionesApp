"""Servicio de aplicación: orquesta jobs, estado en memoria y casos de uso para la UI."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import AppError, BrokerDisconnectedError, BrokerError
from scanner_opciones.domain.models import (
    AccountSummary, OptionContract, Position, RiskStatus, SectorExposure, VixData,
)
from scanner_opciones.jobs.daily_update import DailyUpdater, DailyUpdateReport
from scanner_opciones.jobs.refresh import RefreshJob, RefreshReport
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.portfolio.cushion import build_risk_status
from scanner_opciones.portfolio.leverage import AssignmentExposure, assignment_exposure
from scanner_opciones.portfolio.diversification import WeekExposure, sector_exposure, weekly_sector_exposure
from scanner_opciones.portfolio.simulator import SimulatedTrade, SimulationResult, simulate
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.engine import ScanOutput, ScanResult, list_stored, run_scan
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    BarRepo, ContractRepo, MetaRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)
from scanner_opciones.marketdata.candles import CandleProvider
from scanner_opciones.marketdata.prices import PriceProvider
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.rankedstocks.loader import RankedTable, load_table
from scanner_opciones.watchlist.parser import ParseResult

log = logging.getLogger(__name__)

LAST_FULL_REFRESH = "last_full_refresh_at"  # clave de `meta`: último refresco completo
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
    data_version: int = 0            # sube al terminar un refresco: las páginas abiertas lo consultan para recargarse


class AppService:
    def __init__(
        self,
        gateway: BrokerGateway,
        db: Database,
        settings: Settings,
        now: Callable[[], datetime] = datetime.now,
        market: Optional[MarketCalendar] = None,
        volatility: Optional[VolatilityProvider] = None,
        prices: Optional[PriceProvider] = None,
        candles: Optional[CandleProvider] = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.now = now
        self.market = market or MarketCalendar.from_settings(settings.market, settings.ibkr.delay_minutes)
        self.watchlist = WatchlistRepo(db)
        self.ticker_info = TickerInfoRepo(db)
        self.contracts = ContractRepo(db)
        self.snapshots = SnapshotRepo(db)
        self.meta = MetaRepo(db)
        self.bars = BarRepo(db)
        self.daily = DailyUpdater(gateway, self.watchlist, self.ticker_info,
                                  self.contracts, settings, now, volatility, prices, candles, self.bars)
        self.refresh_job = RefreshJob(
            gateway, self.contracts, self.snapshots, self.ticker_info, settings, now,
            volatility, prices,
        )
        self.state = AppState()
        self.rankedstocks: Optional[RankedTable] = None   # fichero de RankedStocks elegido por el usuario (memoria)
        self.rankedstocks_loaded_at: Optional[datetime] = None
        self.restore_rankedstocks()
        self._lock = asyncio.Lock()  # evita ejecuciones solapadas
        self._background: set = set()

    # ---- RankedStocks: el último fichero cargado sobrevive a los reinicios --------------------
    def _rankedstocks_copy(self) -> Optional[Path]:
        """Copia del último .xlsx cargado, junto a la base de datos (`data/` no se sube al repositorio)."""
        path = self.settings.storage.path
        return None if str(path) == ":memory:" else Path(path).parent / "rankedstocks_last.xlsx"

    def set_rankedstocks(self, table: RankedTable, content: bytes) -> None:
        """Sustituye el fichero cargado: queda en memoria y en disco hasta que se cargue otro."""
        self.rankedstocks, self.rankedstocks_loaded_at = table, self.now()
        self.meta.set("rankedstocks_query", "")   # otro fichero, otras columnas: los filtros anteriores no valen
        if (copy := self._rankedstocks_copy()) is not None:
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(content)
            self.meta.set("rankedstocks_name", table.source)
            self.meta.set("rankedstocks_loaded_at", self.rankedstocks_loaded_at.isoformat())

    def restore_rankedstocks(self) -> None:
        copy = self._rankedstocks_copy()
        name = self.meta.get("rankedstocks_name")
        if copy is None or not name or not copy.is_file():
            return
        try:
            table = load_table(copy)
            loaded_at = datetime.fromisoformat(self.meta.get("rankedstocks_loaded_at") or "")
        except (AppError, ValueError) as exc:
            log.warning("No se pudo recuperar el último fichero de RankedStocks (%s): %s", name, exc)
            return
        self.rankedstocks, self.rankedstocks_loaded_at = replace(table, source=name), loaded_at

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
        # Primero se refresca con lo ya guardado (la pantalla tiene datos enseguida); la actualización
        # diaria va después en segundo plano y, al acabar, cotiza lo que haya cambiado.
        if self._paused() and not self._capture_needed():
            log.info("Mercado cerrado y cotizaciones ya posteriores al último cierre: solo cartera y VIX")
            await self.refresh_all(include_market=False)
        else:
            await self.refresh_all()
        if self.settings.daily_update.run_on_startup:
            self.launch(self.run_daily_then_refresh())

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

    async def replace_watchlist(self, parsed: ParseResult) -> tuple[list[str], list[str], list[str]]:
        """Sustituye la watchlist por la lista dada: quita (con sus contratos) los tickers que no
        están en ella, conserva los que siguen (con sus datos) y añade los nuevos con su actualización
        diaria. Devuelve (nuevos, conservados, quitados). Una lista sin tickers válidos NO vacía la
        watchlist: lanza ValueError."""
        if not parsed.tickers:
            raise ValueError("la lista no contiene ningún ticker válido")
        current = self.watchlist.list()
        wanted = set(parsed.tickers)
        removed = [t for t in current if t not in wanted]
        for ticker in removed:
            self.remove_ticker(ticker)
        kept = [t for t in current if t in wanted]
        new = await self.add_watchlist(parsed)
        return new, kept, removed

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
            "daily_bars": self.bars.purge_except(keep),
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
        self.state.activity = f"Actualización diaria: {i}/{total} tickers (último: {ticker})"

    async def run_daily_then_refresh(
        self, tickers: Optional[list[str]] = None, wait: bool = False, revalidate: bool = False
    ) -> Optional[DailyUpdateReport]:
        """Actualización diaria y, si ha actualizado algo, un refresco para cotizar los contratos nuevos
        sin esperar al siguiente ciclo periódico. Con el mercado cerrado solo se cotizan los contratos
        que nunca se habían cotizado (con datos congelados), no el resto."""
        report = await self.run_daily(tickers, wait=wait, revalidate=revalidate)
        if report is not None and report.updated and self.state.connected:
            if self._paused():
                await self.refresh_new_contracts()   # mercado cerrado: solo los contratos que nunca se cotizaron
            else:
                await self.refresh_all()
        return report

    async def run_daily(
        self, tickers: Optional[list[str]] = None, wait: bool = False, revalidate: bool = False
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
                    report = await self.daily.run(tickers, self._progress, revalidate=revalidate)
                else:
                    report = await self.daily.run_pending(self._progress)
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return None
            finally:
                self.state.activity = None
            self.state.last_daily_report = report
            return report

    def market_open(self) -> bool:
        return self.market.is_open(self.now())

    def _paused(self) -> bool:
        """True si el mercado está cerrado y la configuración pide no cotizar entonces."""
        return self.settings.market.pause_when_closed and not self.market_open()

    def _capture_needed(self) -> bool:
        """¿Falta la captura del cierre? Sí si el último refresco completo (guardado en la base de
        datos, así sobrevive a reinicios) es anterior al último cierre de sesión."""
        raw = self.meta.get(LAST_FULL_REFRESH)
        if raw is None:
            return True
        return datetime.fromisoformat(raw).astimezone() < self.market.last_close(self.now())

    async def refresh_periodic(self) -> bool:
        """Refresco automático según el horario del mercado. Abierto: completo. Cerrado: una única
        captura completa tras el cierre (con datos congelados, para tener el cierre) y después solo
        cartera y VIX, porque precios, IV, cotizaciones y márgenes no pueden cambiar hasta la apertura."""
        if not self._paused() or self._capture_needed():
            return await self.refresh_all()
        log.info("Mercado cerrado: se refrescan solo cartera y VIX")
        return await self.refresh_all(include_market=False)

    async def refresh_all(self, include_market: bool = True) -> bool:
        """Cartera, riesgo, VIX y contratos (`include_market=False`: sin cotizaciones de subyacentes,
        opciones ni márgenes). False si se omitió por solapamiento o falta de conexión."""
        if self.busy:
            log.info("Refresco omitido: hay otra ejecución en curso")
            return False
        async with self._lock:
            try:
                self.state.activity = (
                    "Refrescando cartera, VIX y cotizaciones" if include_market
                    else "Refrescando cartera y VIX (mercado cerrado)"
                )
                self.cleanup_orphans()
                await self._refresh_portfolio()
                await self._refresh_vix()
                if include_market:
                    self.state.last_refresh_report = await self.refresh_job.run()
                    self.meta.set(LAST_FULL_REFRESH, self.now().isoformat())
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            finally:
                self.state.activity = None
            self.state.last_refresh = self.now()
            self.state.data_version += 1
            return True

    async def refresh_new_contracts(self) -> bool:
        """Cotiza (datos congelados del último cierre) solo los contratos guardados que nunca se han
        cotizado y encajan con el filtro inicial. No toca el marcador del último refresco completo."""
        if self.busy:
            log.info("Cotización de contratos nuevos omitida: hay otra ejecución en curso")
            return False
        async with self._lock:
            try:
                self.state.activity = "Cotizando los contratos nuevos (mercado cerrado)"
                self.state.last_refresh_report = await self.refresh_job.run(only_unquoted=True)
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            finally:
                self.state.activity = None
            self.state.data_version += 1
            return True

    def pacing_wait_seconds(self) -> float:
        return self.gateway.pacing_wait_seconds()

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
            self.state.data_version += 1
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
    def criteria(self, **overrides) -> ScanCriteria:
        return criteria_from_settings(self.settings).with_filters(**overrides)

    def scan(self, criteria: ScanCriteria, include_rejections: bool = False) -> ScanOutput:
        snapshots = self.snapshots.all()
        bars = self.bars.all_closes({s.contract.ticker for s in snapshots}) if criteria.technical_active else None
        return run_scan(
            snapshots, self.ticker_info.all(), self.state.positions, criteria,
            self.now().date(), include_rejections, bars, self.settings.scanner.technical,
        )

    def stored_contracts(self) -> list[ScanResult]:
        """Todos los contratos guardados (con o sin cotización), sin aplicar filtros del scanner."""
        return list_stored(
            self.contracts.list(), self.snapshots.all(), self.ticker_info.all(), self.state.positions,
            self.criteria(), self.now().date(),
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
