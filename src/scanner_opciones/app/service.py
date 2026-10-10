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
from scanner_opciones.domain.errors import AppError, BrokerDisconnectedError, BrokerError, VolatilityError, WatchlistError
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
from scanner_opciones.scanner.impact import ImpactReport, impact_report
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    QualityRepo,
    BarRepo, ContractRepo, MetaRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)
from scanner_opciones.marketdata.financials import FinancialsProvider
from scanner_opciones.marketdata.fundamentals import FundamentalsProvider
from scanner_opciones.marketdata.names import CompanyNameProvider
from scanner_opciones.marketdata.candles import CandleProvider
from scanner_opciones.marketdata.prices import PriceProvider
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.rankedstocks.loader import build_table, clean_ticker
from scanner_opciones.universe.sources import MANUAL, SOURCE_COLUMN, Source, identities, load_sources
from scanner_opciones.watchlist.parser import ParseResult

log = logging.getLogger(__name__)

LAST_FULL_REFRESH = "last_full_refresh_at"  # clave de `meta`: último refresco completo
EXCLUDED = "watchlist_excluded"   # meta antigua (lista permanente de excluidos): ya no se usa, se vacía al arrancar
MAX_SOURCE_NAME = 40  # longitud máxima del nombre de una fuente manual
MAX_HISTORY = 200     # cargas que recuerda el historial del Universo
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
    last_account_refresh: Optional[datetime] = None   # último refresco de cuenta, posiciones y VIX
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
        fundamentals: Optional[FundamentalsProvider] = None,
        financials: Optional[FinancialsProvider] = None,
        names: Optional[CompanyNameProvider] = None,
    ) -> None:
        self._db_path = str(getattr(db, "path", ":memory:"))
        self.gateway = gateway
        self.settings = settings
        self.now = now
        self.market = market or MarketCalendar.from_settings(settings.market, settings.ibkr.delay_minutes)
        self.watchlist = WatchlistRepo(db)
        self.ticker_info = TickerInfoRepo(db)
        self.quality = QualityRepo(db)
        self.contracts = ContractRepo(db)
        self.snapshots = SnapshotRepo(db)
        self.meta = MetaRepo(db)
        self.bars = BarRepo(db)
        syncer = ContractSyncer(gateway, self.contracts, settings)  # comparte la cadena en caché
        self.daily = DailyUpdater(gateway, self.watchlist, self.ticker_info, self.contracts, settings,
                                  now, volatility, prices, candles, self.bars, syncer, fundamentals, financials)
        self.refresh_job = RefreshJob(
            gateway, self.contracts, self.snapshots, self.ticker_info, settings, now,
            volatility, prices, syncer, self.market,
        )
        self.state = AppState()
        # ficheros del universo (RankedStocks, HelloStocks): nombre -> (hora de carga, fuentes); en memoria y en disco
        self.universe_files: dict[str, tuple[datetime, tuple[Source, ...]]] = {}
        self.volatility = volatility
        self.names = names
        self.universe_info: dict[str, dict[str, str]] = self._load_info()   # nombre/sector que ninguna fuente trae
        self._has_options: dict[str, bool] = self._load_options_map()   # ticker -> ¿tiene opciones?
        self.manual_sources: dict[str, list[str]] = self._load_manual()   # fuentes con nombre: tickers escritos a mano
        self.source_excluded: dict[str, set[str]] = self._load_excluded()  # tickers quitados a mano de una fuente de fichero
        # aviso de los tickers recién sacados de la watchlist por inservibles (ticker -> motivo); solo en memoria
        self.excluded_notice: dict[str, str] = {}
        if self.meta.get(EXCLUDED):
            self.meta.set(EXCLUDED, "")   # la exclusión ya no es permanente
        self.restore_universe()
        self._lock = asyncio.Lock()  # evita ejecuciones solapadas de los jobs de mercado
        self._financials_running = False
        self._account_lock = asyncio.Lock()  # cuenta, posiciones y VIX: aparte, para que un ciclo largo de mercado no los retrase
        self._background: set = set()

    # ---- Universo: los ficheros cargados sobreviven a los reinicios --------------------------
    def _universe_dir(self) -> Optional[Path]:
        """Copias de los .xlsx cargados, junto a la base de datos (`data/` no se sube al repositorio)."""
        # Se deriva de la base de datos REALMENTE en uso, no de la configuración: un servicio con una base en memoria
        # (tests, scripts de diagnóstico) con la configuración por defecto apuntaría a `data/` y pisaría los ficheros reales.
        return None if self._db_path == ":memory:" else Path(self._db_path).parent / "universe"

    @property
    def universe_sources(self) -> list[Source]:
        sources: list[Source] = []
        for _, srcs in self.universe_files.values():
            for src in srcs:
                skip = self.source_excluded.get(src.name)
                if skip:
                    rows = tuple(r for r in src.table.rows if r.ticker not in skip)
                    if not rows:
                        continue
                    src = replace(src, table=replace(src.table, rows=rows))
                sources.append(src)
        for name, tickers in self.manual_sources.items():
            if tickers:
                raw = [["Ticker", SOURCE_COLUMN]] + [[t, name] for t in tickers]
                sources.append(Source(name, name, build_table(name, raw)))
        return sources

    def _load_manual(self) -> dict[str, list[str]]:
        """{fuente: tickers}. Antes había una única fuente «Manual» guardada como lista."""
        try:
            data = json.loads(self.meta.get("universe_manual") or "{}")
        except (ValueError, TypeError):
            return {}
        if isinstance(data, list):
            return {MANUAL: [str(t) for t in data]} if data else {}
        if not isinstance(data, dict):
            return {}
        return {str(k): [str(t) for t in v] for k, v in data.items() if isinstance(v, list) and v}

    def _save_manual(self) -> None:
        self.meta.set("universe_manual", json.dumps(self.manual_sources))

    def _load_excluded(self) -> dict[str, set[str]]:
        try:
            return {str(k): {str(t) for t in v} for k, v in json.loads(self.meta.get("universe_excluded") or "{}").items()}
        except (ValueError, TypeError, AttributeError):
            return {}

    def _load_info(self) -> dict[str, dict[str, str]]:
        try:
            return {str(t): {str(k): str(v) for k, v in d.items() if v}
                    for t, d in json.loads(self.meta.get("universe_info") or "{}").items()}
        except (ValueError, TypeError, AttributeError):
            return {}

    def universe_identity(self) -> dict[str, tuple[str, str]]:
        """{ticker: (empresa, sector)} de todo el Universo. Primero lo que traen las fuentes (la primera que lo
        aporte), después lo completado a mano (`universe_info`: nombre de tastytrade, sector de IBKR) y, para el
        sector, el de la ficha de IBKR de la watchlist. Cadena vacía si no se sabe."""
        ident = identities(self.universe_sources)
        sectors = {t: i.sector for t, i in self.ticker_info.all().items() if i.sector}
        out = {}
        for ticker, (name, sector) in ident.items():
            extra = self.universe_info.get(ticker, {})
            out[ticker] = (name or extra.get("name", ""), sector or extra.get("sector", "") or sectors.get(ticker, ""))
        return out

    async def complete_info(self, tickers=None) -> int:
        """Completa nombre (tastytrade) y sector (IBKR) de los tickers de las fuentes manuales que ninguna otra
        fuente aporta. Todo es opcional: si el proveedor o IBKR no responden, se queda en blanco. Devuelve cuántos
        datos se añadieron."""
        raw = list(tickers) if tickers is not None else [t for ts in self.manual_sources.values() for t in ts]
        ident = self.universe_identity()
        lookup = {clean_ticker(t): t for t in raw}               # ticker de la app -> como se escribió («BRK.B»)
        no_name = [t for t in lookup if not ident.get(t, ("", ""))[0]]
        no_sector = [t for t in lookup if not ident.get(t, ("", ""))[1]]
        added = 0
        if no_name and self.names is not None:
            try:
                found = await self.names.get_company_names([lookup[t] for t in no_name])
            except Exception as exc:   # el proveedor puede fallar de muchas formas: es un dato opcional
                log.warning("Universo: no se pudieron obtener los nombres de las empresas: %s", exc)
                found = {}
            for raw_ticker, text in found.items():
                self.universe_info.setdefault(clean_ticker(raw_ticker), {})["name"] = text
                added += 1
        failures = 0
        for ticker in no_sector:
            if failures >= 3:                                     # IBKR no responde: no se espera por cada ticker
                break
            try:
                sector, _ = await asyncio.wait_for(self.gateway.get_sector_info(lookup[ticker]), 15)
            except BrokerDisconnectedError:
                break
            except Exception:   # ticker desconocido, tiempo agotado...
                failures += 1
                continue
            if sector:
                self.universe_info.setdefault(ticker, {})["sector"] = sector
                added += 1
        if added:
            self.meta.set("universe_info", json.dumps(self.universe_info))
        return added

    def _save_excluded(self) -> None:
        self.meta.set("universe_excluded", json.dumps({k: sorted(v) for k, v in self.source_excluded.items() if v}))

    def _manual_name(self, source: str) -> str:
        """Nombre válido para una fuente manual: no vacío, corto y distinto de las fuentes de los ficheros."""
        name = " ".join(source.split())
        if not name:
            raise WatchlistError("Pon un nombre a la fuente")
        if len(name) > MAX_SOURCE_NAME:
            raise WatchlistError(f"El nombre de la fuente no puede pasar de {MAX_SOURCE_NAME} caracteres")
        taken = {s.name.casefold() for _, srcs in self.universe_files.values() for s in srcs}
        if name.casefold() in taken:
            raise WatchlistError(f"«{name}» ya es una fuente de un fichero: elige otro nombre")
        return next((n for n in self.manual_sources if n.casefold() == name.casefold()), name)

    def _load_history(self) -> list[dict]:
        try:
            data = json.loads(self.meta.get("universe_history") or "[]")
        except (ValueError, TypeError):
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    @property
    def universe_history(self) -> list[dict]:
        """Historial de cargas del Universo, la más reciente primero: {name, kind, at, count, action}."""
        return self._load_history()

    def record_load(self, name: str, kind: str, count: int, action: str) -> None:
        """Anota una carga (`kind`: «fichero» o «manual»; `action`: cargado, añadido o sustituido)."""
        history = self._load_history()
        history.insert(0, {"name": name, "kind": kind, "at": self.now().isoformat(timespec="seconds"),
                           "count": count, "action": action})
        self.meta.set("universe_history", json.dumps(history[:MAX_HISTORY]))

    async def add_manual_tickers(self, parsed: ParseResult, source: str = MANUAL, replace_source: bool = False) -> dict:
        """Añade a la fuente manual `source` (se crea si no existe) los tickers que no están ya en ella y que tienen
        opciones; con `replace_source` la lista de la fuente pasa a ser exactamente los tickers dados (con opciones).
        Devuelve `source`, `added`, `new_in_universe` (de los añadidos, los que no estaban en ninguna fuente),
        `already` (ticker -> [fuente]), `no_options`, `checked` y `removed` (solo al sustituir)."""
        name = self._manual_name(source)
        in_universe = {row.ticker for src in self.universe_sources for row in src.table.rows}
        old = list(self.manual_sources.get(name, []))
        present = set() if replace_source else set(old)
        already = {t: [name] for t in parsed.tickers if clean_ticker(t) in present or t in present}
        new = [t for t in parsed.tickers if t not in already]
        no_options: list[str] = []
        checked = True
        if new and self.volatility is not None:
            found = await self._with_options([clean_ticker(t) for t in new])
            if found is None:
                checked = False
            else:
                no_options = [t for t in new if clean_ticker(t) not in found]
                new = [t for t in new if clean_ticker(t) in found]
        elif new:
            checked = False
        removed: list[str] = []
        if replace_source:
            if not new:
                raise WatchlistError("No hay ningún ticker válido con el que sustituir la fuente; no se ha cambiado")
            keep = {clean_ticker(t) for t in new}
            removed = [t for t in old if clean_ticker(t) not in keep]
            in_universe -= {clean_ticker(t) for t in old}      # lo que ya traía esta fuente no es nuevo en el Universo
            self.manual_sources[name] = list(new)
            self._save_manual()
        elif new:
            self.manual_sources.setdefault(name, []).extend(new)
            self._save_manual()
        if new:
            self.record_load(name, "manual", len(new), "sustituido" if replace_source else "añadido")
            await self.complete_info(new)
            self.kick_quality_update()
        return {"source": name, "added": new, "new_in_universe": [t for t in new if clean_ticker(t) not in in_universe],
                "already": already, "no_options": no_options, "checked": checked, "removed": removed}

    def remove_source_tickers(self, source: str, tickers) -> int:
        """Quita tickers de una fuente (manual o de fichero); devuelve cuántos. Si se quitan todos, la fuente
        desaparece. En las de fichero se recuerda la exclusión (sobrevive al reinicio) hasta que se cargue un fichero
        nuevo de esa fuente, que la sustituye entera."""
        wanted = set(tickers)
        if source in self.manual_sources:
            kept = [t for t in self.manual_sources[source] if clean_ticker(t) not in wanted and t not in wanted]
            removed = len(self.manual_sources[source]) - len(kept)
            if kept:
                self.manual_sources[source] = kept
            else:
                del self.manual_sources[source]
            self._save_manual()
            return removed
        current = next((s for s in self.universe_sources if s.name == source), None)
        if current is None:
            raise WatchlistError(f"La fuente «{source}» no existe")
        hit = {r.ticker for r in current.table.rows} & wanted
        if hit:
            self.source_excluded.setdefault(source, set()).update(hit)
            self._save_excluded()
        return len(hit)

    def remove_source(self, source: str) -> int:
        """Quita todos los tickers de la fuente."""
        current = next((s for s in self.universe_sources if s.name == source), None)
        if current is None:
            raise WatchlistError(f"La fuente «{source}» no existe")
        return self.remove_source_tickers(source, [r.ticker for r in current.table.rows])

    async def _with_options(self, tickers: list[str]) -> Optional[set[str]]:
        """Tickers (de los dados) que tienen opciones; `None` si no se puede comprobar. Recuerda el resultado."""
        if self.volatility is None:
            return None
        pending = sorted(set(tickers) - self._has_options.keys())
        if pending:
            try:
                found = await self.volatility.get_iv_metrics(pending)
            except VolatilityError as exc:
                log.warning("Universo: no se pudo comprobar qué tickers tienen opciones: %s", exc)
                return None
            self._has_options.update({t: t in found for t in pending})
            self.meta.set("universe_options", json.dumps(self._has_options))
        return {t for t in tickers if self._has_options.get(t)}

    def _load_options_map(self) -> dict[str, bool]:
        try:
            return {str(t): bool(v) for t, v in json.loads(self.meta.get("universe_options") or "{}").items()}
        except (ValueError, AttributeError):
            return {}

    async def prune_without_options(self, sources: list[Source]) -> tuple[list[Source], int, bool]:
        """Quita de las fuentes los tickers sin opciones. tastytrade solo da IV Rank/Percentil a los que cotizan
        opciones, así que eso se usa de criterio; cada ticker se comprueba una vez y se recuerda (también en disco).
        Devuelve (fuentes, tickers quitados, ¿se pudo comprobar?); si no se puede, las fuentes quedan intactas."""
        if await self._with_options(sorted({r.ticker for src in sources for r in src.table.rows})) is None:
            return sources, 0, False
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
        self.record_load(file, "fichero", sum(len(s.table.rows) for s in sources), "cargado")
        if any(self.source_excluded.pop(n, None) for n in names):    # un fichero nuevo trae la lista entera
            self._save_excluded()
        self.kick_quality_update()
        if (folder := self._universe_dir()) is not None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / file).write_bytes(content)
            self._save_universe_index()

    def _drop_universe_file(self, file: str) -> None:
        self.universe_files.pop(file, None)
        if (folder := self._universe_dir()) is not None:
            (folder / file).unlink(missing_ok=True)

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
        history = asyncio.ensure_future(self._update_history_and_fundamentals())
        try:
            await self.gateway.connect()
        except BrokerError as exc:
            await history
            self.state.connected = False
            self.state.errors["connection"] = str(exc)
            return
        await history
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
        self.update_financials_in_background()
        if self.manual_sources:
            self.launch(self.complete_info())
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
    def exclude_unsupported(self, report: DailyUpdateReport) -> list[str]:
        """Saca de la watchlist (con sus contratos y ficha) los tickers que la actualización diaria ha
        dado por inservibles y los añade al aviso (con el motivo) hasta que el usuario lo cierre. No se
        recuerdan: si se vuelven a añadir, la siguiente actualización los saca y avisa otra vez."""
        for ticker, reason in report.unsupported.items():
            self.remove_ticker(ticker)
            self.excluded_notice[ticker] = reason
            log.warning("%s excluido de la watchlist: %s", ticker, reason)
        return list(report.unsupported)

    def dismiss_excluded_notice(self) -> None:
        """El usuario cierra el aviso de tickers excluidos."""
        self.excluded_notice.clear()

    async def add_watchlist(self, parsed: ParseResult, background: bool = False) -> list[str]:
        """Añade tickers y lanza la actualización diaria de los nuevos (RF-05). `background=True` (la interfaz) no la
        espera: la lanza en segundo plano, en cola tras lo que esté en curso, y la cabecera muestra el progreso."""
        new = self.watchlist.add(parsed.tickers, self.now())
        if new and self.state.connected:
            if background:
                self.state.activity = self.state.activity or f"En cola: actualización diaria de {len(new)} tickers nuevos"
                self.launch(self.run_daily_then_refresh(new, wait=True))
            else:
                await self.run_daily(new)
        return new

    async def replace_watchlist(
        self, parsed: ParseResult, background: bool = False,
    ) -> tuple[list[str], list[str], list[str]]:
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
        new = await self.add_watchlist(parsed, background)
        return new, kept, removed

    def remove_ticker(self, ticker: str) -> dict[str, int]:
        """Quita el ticker de la watchlist y borra sus contratos (con sus cotizaciones) y su ficha.
        Se conserva el historial de IV: ahorra descargarlo si se vuelve a añadir."""
        self.watchlist.remove(ticker)
        had_info = self.ticker_info.get(ticker) is not None
        self.ticker_info.delete(ticker)
        return {"contracts": self.contracts.delete_for_ticker(ticker), "ticker_info": int(had_info)}

    def quality_scope(self) -> list[str]:
        """Tickers de los que se guardan datos de calidad: la watchlist y todo el Universo (ficheros y manuales), para
        poder filtrar ANTES de decidir qué entra en la watchlist."""
        tickers = set(self.watchlist.list())
        tickers.update(row.ticker for src in self.universe_sources for row in src.table.rows)
        return sorted(tickers)

    def kick_quality_update(self) -> None:
        """Descarga los datos de calidad de lo que falte (tickers recién cargados) sin esperar. Sin bucle de eventos
        en marcha (p. ej. al restaurar el Universo en el constructor) no hace nada: ya se hará en el arranque."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.launch(self._update_fundamentals())
        self.update_financials_in_background()

    def cleanup_orphans(self) -> dict[str, int]:
        """Borra contratos y fichas de tickers que ya no están en la watchlist (p. ej. quitados
        con una versión anterior). Barato: se llama al arrancar y antes de cada refresco."""
        keep = self.watchlist.list()
        removed = {
            "contracts": self.contracts.purge_except(keep),
            "ticker_info": self.ticker_info.purge_except(keep),
            "daily_bars": self.bars.purge_except(keep),
            "quality": self.quality.purge_except(self.quality_scope()),
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
            self.exclude_unsupported(report)
            self.state.last_daily_report = report
            await self._update_history()
            await self._update_fundamentals()
            self.update_financials_in_background()   # las fichas de los tickers nuevos ya existen
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

    async def _update_history_and_fundamentals(self) -> None:
        await self._update_history()
        await self._update_fundamentals()

    async def _update_fundamentals(self) -> None:
        """Datos de calidad de toda la watchlist (EPS, trimestres, capitalización, liquidez, resultados). Un fallo
        no interrumpe nada: los filtros de calidad trabajan con lo guardado."""
        try:
            await self.daily.update_fundamentals(self.quality_scope())
        except Exception:
            log.exception("No se pudieron actualizar los datos de calidad")
        finally:
            self.state.data_version += 1

    def update_financials_in_background(self) -> None:
        """Lanza la consulta a SEC EDGAR (la primera vez dura minutos) sin esperar y sin solaparse consigo misma."""
        if self.daily.financials is None or self._financials_running:
            return
        self._financials_running = True

        async def run() -> None:
            try:
                if await self.daily.update_financials(self.quality_scope()):
                    self.state.data_version += 1
            except Exception:
                log.exception("No se pudieron actualizar el balance y el flujo de caja")
            finally:
                self._financials_running = False

        self.launch(run())

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
        self.update_financials_in_background()   # no hace nada si no hay datos viejos
        if not self._paused() or self._capture_needed():
            return await self.refresh_all()
        log.info("Mercado cerrado: se refrescan solo cartera y VIX")
        return await self.refresh_all(include_market=False)

    async def refresh_all(self, include_market: bool = True) -> bool:
        """Cuenta, riesgo y VIX más los contratos (`include_market=False`: solo cuenta, riesgo y VIX, sin
        cotizaciones de subyacentes, opciones ni márgenes). La cuenta se refresca a la vez que el mercado y con
        su propio bloqueo. False si se omitió por solapamiento o falta de conexión."""
        if not include_market:
            ok = await self.refresh_account()
            if ok:   # mercado cerrado: el refresco solo de cuenta es el refresco del ciclo
                self.state.last_refresh = self.now()
                self.state.data_version += 1
            return ok
        if self.busy:
            log.info("Refresco omitido: hay otra ejecución en curso")
            return False
        async with self._lock:
            account = asyncio.ensure_future(self.refresh_account())   # ~1 s; no espera al mercado
            try:
                self.state.activity = "Refrescando cartera, VIX y cotizaciones"
                self.cleanup_orphans()
                self.state.last_refresh_report = await self.refresh_job.run()
                self.record_today_prices()
                self.meta.set(LAST_FULL_REFRESH, self.now().isoformat())
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            finally:
                self.state.activity = None
                await asyncio.gather(account, return_exceptions=True)
            self.state.last_refresh = self.now()
            self.state.data_version += 1
            return True

    async def refresh_account(self) -> bool:
        """Cuenta, posiciones, riesgo y VIX. Va por su cuenta (bloqueo propio): no espera a la actualización diaria
        ni a un ciclo largo de cotizaciones. False si ya hay uno en curso o se perdió la conexión."""
        if self._account_lock.locked():
            return False
        async with self._account_lock:
            try:
                await asyncio.gather(self._refresh_portfolio(), self._refresh_vix())
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            self.state.last_account_refresh = self.now()
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
            self.state.account = await asyncio.wait_for(self.gateway.get_account_summary(), timeout=STEP_TIMEOUT_SECONDS)
            self.state.positions = await asyncio.wait_for(self.gateway.get_positions(), timeout=STEP_TIMEOUT_SECONDS)
            self.state.risk = build_risk_status(self.state.account, self.settings.risk.cushion_thresholds)
            self.state.errors.pop("portfolio", None)
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            self.state.errors["portfolio"] = str(exc)
        except asyncio.TimeoutError:
            self.state.errors["portfolio"] = "Timeout obteniendo la cuenta (¿TWS ha perdido la conexión con IBKR?)"

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
            self.settings.scanner.quality.exempt_sectors,
        )

    def scan_impact(self, criteria: ScanCriteria) -> ImpactReport:
        """Cuánto descarta cada filtro activo (ver `scanner/impact.py`)."""
        snapshots = self.snapshots.all()
        bars = self.bars.all_closes({s.contract.ticker for s in snapshots}) if criteria.technical_active else None
        return impact_report(
            snapshots, self.ticker_info.all(), criteria, self.now().date(), bars, self.settings.scanner.technical,
            self.settings.scanner.quality.exempt_sectors,
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
