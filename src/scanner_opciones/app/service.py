"""Servicio de aplicación: orquesta jobs, estado en memoria y casos de uso para la UI."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import AppError, BrokerDisconnectedError, BrokerError, VolatilityError
from scanner_opciones.domain.models import (
    AccountSummary, OptionContract, Position, RiskStatus, SectorExposure, VixData,
)
from scanner_opciones.jobs.contract_sync import ContractSyncer
from scanner_opciones.jobs.daily_update import DailyUpdater, DailyUpdateReport
from scanner_opciones.jobs.refresh import RefreshJob, RefreshReport
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.portfolio.cushion import build_risk_status
from scanner_opciones.portfolio.leverage import AssignmentExposure, assignment_exposure
from scanner_opciones.portfolio.diversification import WeekExposure, sector_exposure, weekly_sector_exposure
from scanner_opciones.portfolio.simulator import SimulatedTrade, SimulationResult, simulate
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.engine import ScanOutput, run_scan
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    BarRepo, ContractRepo, MetaRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)
from scanner_opciones.marketdata.candles import CandleProvider
from scanner_opciones.marketdata.prices import PriceProvider
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.universe.sources import Source, load_sources
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
        syncer = ContractSyncer(gateway, self.contracts, settings)  # comparte la cadena en caché
        self.daily = DailyUpdater(gateway, self.watchlist, self.ticker_info, self.contracts, settings,
                                  now, volatility, prices, candles, self.bars, syncer)
        self.refresh_job = RefreshJob(
            gateway, self.contracts, self.snapshots, self.ticker_info, settings, now,
            volatility, prices, syncer,
        )
        self.state = AppState()
        # ficheros del universo (RankedStocks, HelloStocks): nombre -> (hora de carga, fuentes); en memoria y en disco
        self.universe_files: dict[str, tuple[datetime, tuple[Source, ...]]] = {}
        self.volatility = volatility
        self._has_options: dict[str, bool] = self._load_options_map()   # ticker -> ¿tiene opciones?
        self.restore_universe()
        self._lock = asyncio.Lock()  # evita ejecuciones solapadas
        self._background: set = set()

    # ---- Universo: los ficheros cargados sobreviven a los reinicios --------------------------
    def _universe_dir(self) -> Optional[Path]:
        """Copias de los .xlsx cargados, junto a la base de datos (`data/` no se sube al repositorio)."""
        path = self.settings.storage.path
        return None if str(path) == ":memory:" else Path(path).parent / "universe"

    @property
    def universe_sources(self) -> list[Source]:
        return [src for _, sources in self.universe_files.values() for src in sources]

    def _load_options_map(self) -> dict[str, bool]:
        try:
            return {str(t): bool(v) for t, v in json.loads(self.meta.get("universe_options") or "{}").items()}
        except (ValueError, AttributeError):
            return {}

    async def prune_without_options(self, sources: list[Source]) -> tuple[list[Source], int, bool]:
        """Quita de las fuentes los tickers sin opciones. tastytrade solo da IV Rank/Percentil a los que cotizan
        opciones, así que eso se usa de criterio; cada ticker se comprueba una vez y se recuerda (también en disco).
        Devuelve (fuentes, tickers quitados, ¿se pudo comprobar?); si no se puede, las fuentes quedan intactas."""
        if self.volatility is None:
            return sources, 0, False
        pending = sorted({r.ticker for src in sources for r in src.table.rows} - self._has_options.keys())
        if pending:
            try:
                found = await self.volatility.get_iv_metrics(pending)
            except VolatilityError as exc:
                log.warning("Universo: no se pudo comprobar qué tickers tienen opciones: %s", exc)
                return sources, 0, False
            self._has_options.update({t: t in found for t in pending})
            self.meta.set("universe_options", json.dumps(self._has_options))
        before = {r.ticker for src in sources for r in src.table.rows}
        pruned = self._prune(sources)
        return pruned, len(before - {r.ticker for src in pruned for r in src.table.rows}), True

    def _prune(self, sources: list[Source]) -> list[Source]:
        """Descarta las filas de tickers que se sabe que no tienen opciones (los no comprobados se conservan)."""
        out = []
        for src in sources:
            rows = tuple(r for r in src.table.rows if self._has_options.get(r.ticker, True))
            if rows:
                out.append(replace(src, table=replace(src.table, rows=rows)))
        return out

    def _save_universe_index(self) -> None:
        self.meta.set("universe_files", json.dumps({name: at.isoformat() for name, (at, _) in self.universe_files.items()}))

    def set_universe_file(self, file: str, sources: list[Source], content: bytes) -> None:
        """Añade el fichero al universo. Sustituye al que lleve el mismo nombre y a los que aporten alguna de
        sus fuentes (una descarga más reciente de HelloStocks o RankedStocks reemplaza a la anterior)."""
        names = {src.name for src in sources}
        for old, (_, olds) in list(self.universe_files.items()):
            if old == file or names & {s.name for s in olds}:
                self._drop_universe_file(old)
        self.universe_files[file] = (self.now(), tuple(sources))
        if (folder := self._universe_dir()) is not None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / file).write_bytes(content)
            self._save_universe_index()

    def _drop_universe_file(self, file: str) -> None:
        self.universe_files.pop(file, None)
        if (folder := self._universe_dir()) is not None:
            (folder / file).unlink(missing_ok=True)

    def remove_universe_file(self, file: str) -> None:
        self._drop_universe_file(file)
        if self._universe_dir() is not None:
            self._save_universe_index()

    def restore_universe(self) -> None:
        folder = self._universe_dir()
        try:
            index = json.loads(self.meta.get("universe_files") or "{}")
        except ValueError:
            index = {}
        if folder is None:
            return
        for file, at in index.items():
            try:
                self.universe_files[file] = (datetime.fromisoformat(at), tuple(self._prune(load_sources(folder / file, file))))
            except (AppError, ValueError) as exc:
                log.warning("No se pudo recuperar el fichero del universo %s: %s", file, exc)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ---- ciclo de vida ---------------------------------------------------------------------
    async def start(self) -> None:
        # El histórico de cierres solo depende de tastytrade y se guarda en la base de datos: se completa lo
        # primero (unos segundos), sin esperar al broker ni al primer refresco, que tarda minutos.
        await self._update_history()
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
            self.daily.syncer.gateway = new_gateway
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
            await self._update_history()
            return report

    async def _update_history(self) -> None:
        """Histórico de cierres de toda la watchlist (los que ya están al día no piden nada). Un fallo no
        interrumpe la actualización diaria: los filtros técnicos avisan de que falta histórico."""
        self.state.activity = "Descargando histórico de cierres"
        try:
            await self.daily.update_history(self.watchlist.list())
            self.record_today_prices()
        except Exception:
            log.exception("No se pudo actualizar el histórico de cierres")
        finally:
            self.state.activity = None
            self.state.data_version += 1

    def record_today_prices(self) -> int:
        """Guarda el último precio de cada ticker como cierre provisional de la sesión de hoy en el histórico (lo
        sustituye el cierre oficial al día siguiente). Solo si hoy hay sesión y el precio es de hoy. Devuelve
        cuántos tickers se guardaron."""
        day = self.market.session_day(self.now())
        if day is None:
            return 0
        saved = 0
        for ticker, info in self.ticker_info.all().items():
            if info.underlying_price and info.price_at and info.price_at.astimezone(self.market.tz).date() == day:
                self.bars.upsert_provisional(ticker, day, info.underlying_price)
                saved += 1
        return saved

    def history_coverage(self) -> tuple[int, int]:
        """(tickers de la watchlist con histórico de cierres, tickers de la watchlist)."""
        tickers = self.watchlist.list()
        return len(self.bars.last_days(tickers)), len(tickers)

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
                    self.record_today_prices()
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
