"""Servicio de aplicación: orquesta jobs, estado en memoria y casos de uso para la UI."""
from __future__ import annotations

import time
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import ScannerPreset, Settings, SpreadSettings, StrategySettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.errors import AppError, BrokerDisconnectedError, BrokerError, VolatilityError, WatchlistError
from scanner_opciones.domain.models import (
    AccountSummary, ContractSnapshot, OptionContract, Position, RiskStatus, SectorExposure, VixData,
)
from scanner_opciones.app import portfolio_cache, vix_cache
from scanner_opciones.jobs.contract_sync import ContractSyncer
from scanner_opciones.jobs.daily_update import DailyUpdater, DailyUpdateReport
from scanner_opciones.jobs.refresh import RefreshJob, RefreshReport
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.portfolio.cushion import build_risk_status
from scanner_opciones.portfolio.leverage import AssignmentExposure, assignment_exposure
from scanner_opciones.portfolio.diversification import WeekExposure, sector_exposure, weekly_sector_exposure
from scanner_opciones.community.data import CommunityData, CommunityError, load_community
from scanner_opciones.community.fit import strategy_fits
from scanner_opciones.community.ideas import IdeaView, evaluate_ideas, idea_key, past_trade, snapshot_key
from scanner_opciones.portfolio.openings import OpeningsData, OpeningsError, load_openings, norm_ticker
from scanner_opciones.portfolio.classify import FALLBACK as FALLBACK_PROFILE, classify_dte
from scanner_opciones.portfolio.management import PROFILES as PUT_PROFILE_NAMES, put_key as put_key_of
from scanner_opciones.portfolio.spreads import with_spreads
from scanner_opciones.portfolio.simulator import SimulatedTrade, SimulationResult, simulate
from scanner_opciones.scanner import profiles as profiles_mod
from scanner_opciones.scanner.profiles import Profile as ScannerProfile
from scanner_opciones.scanner.filters import iv_change_available, iv_hv_available
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.engine import ScanOutput, run_scan
from scanner_opciones.scanner.spreads import build_mixed_sets, build_profile_rows
from scanner_opciones.scanner.tech_summary import TechSummarizer, TechSummary
from scanner_opciones.scanner.impact import ImpactReport, impact_report
from scanner_opciones.scanner.spread_check import CheckRow, spread_check
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    QualityRepo,
    BarRepo, ContractRepo, MetaRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.filings import FilingAlert
from scanner_opciones.tortuga.radar import RadarData, RadarError, load_radar, passes as radar_passes
from scanner_opciones.tortuga.routines import BY_KEY as TORTUGA_ROUTINES, RoutineRunner
from scanner_opciones.marketdata.financials import FinancialsProvider
from scanner_opciones.marketdata.fundamentals import FundamentalsProvider
from scanner_opciones.marketdata.names import CompanyNameProvider
from scanner_opciones.marketdata.candles import CandleProvider
from scanner_opciones.marketdata.prices import PriceProvider
from scanner_opciones.marketdata.volatility import VolatilityProvider
from scanner_opciones.rankedstocks.loader import build_table, clean_ticker
from scanner_opciones.universe.sources import MANUAL, SOURCE_COLUMN, Source, identities, load_sources
from scanner_opciones.watchlist.parser import ParseResult, parse_tokens

log = logging.getLogger(__name__)

LAST_FULL_REFRESH = "last_full_refresh_at"  # clave de `meta`: último refresco completo
EXCLUDED = "watchlist_excluded"   # meta antigua (lista permanente de excluidos): ya no se usa, se vacía al arrancar
VIX_CACHE = "vix_cache"   # clave de `meta`: último VIX leído (ver `app/vix_cache.py`)
PORTFOLIO_CACHE = "portfolio_cache"   # clave de `meta`: última cuenta y posiciones de TWS (se muestran sin conexión)
SPREAD_SOURCES = "spread_sources"       # clave de `meta`: fuentes del Universo marcadas a mano como aptas para spreads (JSON, lista de nombres)
SPREAD_UNIVERSE_SYNCED = "spread_universe_synced"   # (obsoleta) marca del antiguo universo para spreads; se vacía en `remove_legacy_spread_universe`
RADAR_SYNCED = "radar_spreads_synced"   # clave de `meta`: radar de Bull Put Spreads (y umbral) ya volcado en la fuente TortugaSpreads
COMMUNITY_SYNCED = "community_synced"   # clave de `meta`: `generated_at` del JSON de la comunidad ya volcado en la fuente del Universo
COMMUNITY_PERM = "community_perm"   # clave de `meta`: trader(es) y mejor estrategia de cada ticker de la fuente permanente (JSON)
SCANNER_PROFILES = "scanner_profiles"   # clave de `meta`: perfiles del Scanner (JSON, `scanner/profiles.py`); vacío = los que salen de config.yaml
PUT_OPENED = "put_opened"   # clave de `meta`: día en que la app vio por primera vez cada put vendida (JSON {put_key: AAAA-MM-DD})
PUT_PROFILES = "put_profiles"   # clave de `meta`: perfil de gestión elegido para cada put vendida (JSON {put_key: perfil})
SCANNER_USER_CONFIG = "scanner_user_config"   # clave de `meta`: presets y estrategia editados desde la aplicación (JSON)
SCAN_MEMO_SECONDS = 120.0                           # vida de las lecturas reutilizadas entre cálculos del Scanner (`_memo`)
KEPT = "no se descartan por este filtro"       # efecto de un dato que falta en el aviso de `filter_data_gaps`
MAX_SOURCE_NAME = 40  # longitud máxima del nombre de una fuente manual
MAX_HISTORY = 200     # cargas que recuerda el historial del Universo
STEP_TIMEOUT_SECONDS = 90  # un paso de red colgado no debe bloquear el refresco para siempre


@dataclass
class SelectedContract:
    ticker: str
    expiry: date
    strike: float
    quantity: int = 1
    long_strike: Optional[float] = None   # spread de puts: strike de la comprada
    max_loss: Optional[float] = None      # spread: pérdida máxima por spread en $ (hace de margen)


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


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


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
        tortuga: Optional[RoutineRunner] = None,
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
        self.filing_alerts = self.daily.filing_alerts
        self.refresh_job = RefreshJob(
            gateway, self.contracts, self.snapshots, self.ticker_info, settings, now,
            volatility, prices, syncer, self.market,
        )
        self.state = AppState()
        self._scan_memo: dict[tuple, tuple[float, object]] = {}
        self._openings: Optional[tuple[float, OpeningsData]] = None
        self._openings_error: Optional[str] = None
        self.profile_provider: Optional[Callable[[], tuple[list[ScannerProfile], str]]] = None   # lo pone la interfaz web (perfiles guardados o derivados)
        self._yaml_scanner = (settings.scanner.presets, settings.scanner.strategy, settings.scanner.spreads)   # los de config.yaml, para restaurarlos
        self._load_scanner_config()
        self._load_portfolio()
        self._load_vix()
        # ficheros del universo (RankedStocks, HelloStocks): nombre -> (hora de carga, fuentes); en memoria y en disco
        self.universe_files: dict[str, tuple[datetime, tuple[Source, ...]]] = {}
        self.volatility = volatility
        self.names = names
        self.universe_info: dict[str, dict[str, str]] = self._load_info()   # nombre/sector que ninguna fuente trae
        self._has_options: dict[str, bool] = self._load_options_map()   # ticker -> ¿tiene opciones?
        self._community: Optional[tuple[float, CommunityData]] = None    # (mtime del JSON, datos) de la comunidad de Tortuga
        self._community_error: Optional[str] = None
        self._community_syncing = False
        self.tortuga = tortuga                    # rutinas que lanzan la CLI de Claude (None en los tests: nunca se ejecuta nada externo)
        self._tortuga_busy = False
        self._radar: Optional[tuple[float, RadarData]] = None
        self._radar_error: Optional[str] = None
        self._radar_syncing = False
        # cotizaciones en directo de las ideas fuera de lo guardado: (hora, generated_at del JSON, snapshots, fichas, cierres)
        self._community_live: Optional[tuple[datetime, str, dict, dict, dict]] = None
        self._community_live_running = False
        self.manual_sources: dict[str, list[str]] = self._load_manual()   # fuentes con nombre: tickers escritos a mano
        self._rename_legacy_community_source()
        self.source_excluded: dict[str, set[str]] = self._load_excluded()  # tickers quitados a mano de una fuente de fichero
        # aviso de los tickers recién sacados de la watchlist por inservibles (ticker -> motivo); solo en memoria
        self.excluded_notice: dict[str, str] = {}
        if self.meta.get(EXCLUDED):
            self.meta.set(EXCLUDED, "")   # la exclusión ya no es permanente
        self.restore_universe()
        self._lock = asyncio.Lock()  # evita ejecuciones solapadas de los jobs de mercado
        self._financials_running = False
        self._quality_running = False
        self._starting = False       # intento de conexión en curso
        self._session_begun = False  # ya se hizo el primer refresco/actualización diaria de la sesión (con o sin TWS)
        self._iv_stats_running = False
        self._tasty_done = False     # histórico y calidad de tastytrade ya descargados en esta ejecución
        self._tasty_task: "asyncio.Future | None" = None   # descarga de arranque de tastytrade, en segundo plano
        self._tasty_again = False    # una actualización diaria acabó mientras la del arranque seguía: que la repita al terminar
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
        perm_name, perm = self.settings.community.perm_source_name, self.community_perm()
        for name, tickers in self.manual_sources.items():
            if not tickers:
                continue
            if name == perm_name:      # la fuente permanente lleva además el trader y la estrategia que más filtros cumplía
                raw = [["Ticker", SOURCE_COLUMN, "Trader", "Estrategia", "Filtros"]]
                for t in tickers:
                    p = perm.get(t, {})
                    raw.append([t, name, ", ".join(p.get("traders", [])), p.get("strategy", ""),
                                f"{p['passed']}/{p['total']}" if "passed" in p else ""])
            else:
                raw = [["Ticker", SOURCE_COLUMN]] + [[t, name] for t in tickers]
            sources.append(Source(name, name, build_table(name, raw)))
        return sources

    def ticker_lists(self) -> dict[str, list[str]]:
        """{ticker: nombres de las fuentes del Universo (ficheros y manuales) en las que está}."""
        out: dict[str, list[str]] = {}
        for src in self.universe_sources:
            for row in src.table.rows:
                out.setdefault(row.ticker, []).append(src.name)
        return out

    def _rename_legacy_community_source(self) -> None:
        """La fuente de los tickers diarios de la comunidad se llamaba «Tortuga»; ahora `community.source_name`."""
        cfg = self.settings.community
        old = cfg.legacy_source_name
        if old != cfg.source_name and old in self.manual_sources and cfg.source_name not in self.manual_sources:
            self.manual_sources[cfg.source_name] = self.manual_sources.pop(old)
            self._save_manual()
            log.info("Fuente del Universo «%s» renombrada a «%s»", old, cfg.source_name)

    def community_perm(self) -> dict[str, dict]:
        """{ticker: {traders, strategy, passed, total}} de la fuente permanente de la comunidad."""
        try:
            data = json.loads(self.meta.get(COMMUNITY_PERM) or "{}")
        except (ValueError, TypeError):
            return {}
        return {str(t): d for t, d in data.items() if isinstance(d, dict)} if isinstance(data, dict) else {}

    async def add_community_perm(self, keys) -> dict:
        """Añade a la fuente permanente los tickers de las ideas marcadas (`idea_key`). De cada ticker guarda el trader (si hay
        varias ideas, todos) y la estrategia del Scanner que más filtros cumple hoy (a igualdad, la de mayor proporción; si el
        ticker ya estaba, se conserva la mejor de las dos). Devuelve el resultado de `add_manual_tickers` más `selected`."""
        wanted = set(keys)
        views = [v for v in self.community_ideas() if idea_key(v.idea) in wanted]
        if not views:
            raise WatchlistError("No hay ninguna idea marcada (o ya no está en los datos de la comunidad)")
        perm = self.community_perm()
        for v in views:
            entry = perm.setdefault(v.idea.ticker, {"traders": []})
            if v.idea.trader not in entry["traders"]:
                entry["traders"] = sorted([*entry["traders"], v.idea.trader], key=str.casefold)
            for f in v.fits:
                if f.total and (f.passed, f.passed / f.total) > (entry.get("passed", -1), entry["passed"] / entry["total"] if "passed" in entry else -1):
                    entry.update(strategy=f.name, passed=f.passed, total=f.total)
        tickers = sorted({v.idea.ticker for v in views})
        res = await self.add_manual_tickers(parse_tokens(tickers), self.settings.community.perm_source_name)
        in_source = {clean_ticker(t) for t in self.manual_sources.get(res["source"], [])}
        self.meta.set(COMMUNITY_PERM, json.dumps({t: d for t, d in perm.items() if clean_ticker(t) in in_source}))
        self.state.data_version += 1
        return {**res, "selected": tickers, "perm": {t: perm[t] for t in tickers if t in perm}}

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
        `already` (ticker -> [fuente]), `no_options`, `checked`, y al sustituir `removed` (los que ya no están) y `kept`
        (los que ya tenía la fuente). «Nuevo en el Universo» = ni en otra fuente ni en la lista anterior de esta."""
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
        kept: list[str] = []
        if replace_source:
            if not new:
                raise WatchlistError("No hay ningún ticker válido con el que sustituir la fuente; no se ha cambiado")
            keep = {clean_ticker(t) for t in new}
            removed = [t for t in old if clean_ticker(t) not in keep]
            kept = [t for t in new if clean_ticker(t) in {clean_ticker(o) for o in old}]
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
                "already": already, "no_options": no_options, "checked": checked, "removed": removed, "kept": kept}

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
        if self._starting:
            return
        self.split_tastytrade_profile()
        self.remove_legacy_spread_universe()
        self._starting = True
        try:
            connected = await self._connect()
        finally:
            self._starting = False
        if connected:
            self.state.connected = True
            self.state.errors.pop("connection", None)
            if self._session_begun and self._offline_capable():
                await self._resume_after_reconnect()
                return
        elif not self._offline_capable():
            return
        await self._begin_session()

    async def reconnect(self) -> bool:
        """Reintento de conexión con el broker (lo llama el bucle de reconexión). No hace nada si ya está conectado
        o hay un intento en curso. No repite la parte de tastytrade (histórico y calidad) si ya se hizo."""
        if self.state.connected or self._starting:
            return False
        await self.gateway.disconnect()     # libera un socket a medias antes de volver a conectar
        await self.start()
        return self.state.connected

    async def _connect(self) -> bool:
        # El histórico de cierres, los datos de calidad y la IV solo dependen de tastytrade y se guardan en la base de datos: se
        # descargan EN SEGUNDO PLANO, a la vez que se conecta con el broker. Antes se esperaba a que acabaran (unos segundos
        # con el histórico al día) y, con máximos y mínimos por rellenar y reintentos de velas, tardó 5 minutos en marcar
        # «conectado» aunque TWS ya lo estuviera. Solo una vez: los reintentos de conexión no lo repiten. La cabecera indica
        # la actividad mientras dura.
        if not self._tasty_done and not self._tasty_running():
            self._tasty_task = self.launch(self._tasty_startup())
        try:
            await self.gateway.connect()
        except BrokerError as exc:
            self.state.connected = False
            self.state.errors["connection"] = str(exc)
            return False
        return True

    def _tasty_running(self) -> bool:
        return self._tasty_task is not None and not self._tasty_task.done()

    async def _tasty_startup(self) -> None:
        """Descarga de arranque de tastytrade: histórico, calidad e IV. Si una actualización diaria acabó mientras tanto, repite
        histórico y calidad al terminar (los tickers nuevos que añadió)."""
        try:
            await self._update_history_and_fundamentals()
            while self._tasty_again:
                self._tasty_again = False
                await self._update_history()
                await self._update_fundamentals()
        finally:
            self._tasty_done = True

    def _offline_capable(self) -> bool:
        """¿Puede el gateway servir los datos de mercado sin TWS (tastytrade)? Entonces, sin conexión, se sigue
        actualizando todo lo que no sea de TWS (cuenta, posiciones, márgenes, sector) y se muestra lo último guardado."""
        return bool(getattr(self.gateway, "market_offline_capable", False))

    async def _begin_session(self) -> None:
        """Primer refresco y actualización diaria de la sesión (con o sin TWS)."""
        self._session_begun = True
        self.cleanup_orphans()
        # Primero se refresca con lo ya guardado (la pantalla tiene datos enseguida); la actualización
        # diaria va después en segundo plano y, al acabar, cotiza lo que haya cambiado.
        if self._paused() and not self._capture_needed():
            log.info("Mercado cerrado y cotizaciones ya posteriores al último cierre: solo cartera y VIX")
            await self.refresh_all(include_market=False)
        else:
            await self.refresh_all()
        self.update_financials_in_background()
        self.sync_community_in_background()
        if self.manual_sources:
            self.launch(self.complete_info())
        if self.settings.daily_update.run_on_startup:
            self.launch(self.run_daily_then_refresh())

    async def _resume_after_reconnect(self) -> None:
        """TWS vuelve tras una sesión que ya se había iniciado sin él: no se repite la actualización diaria ni el
        refresco de mercado (tastytrade ya los hizo). Solo se piden a TWS sus datos: cuenta y posiciones, el sector
        de lo que no lo tiene y los márgenes que faltan o caducaron."""
        log.info("TWS disponible: se completan cuenta, posiciones, sectores y márgenes")
        await self.refresh_account()
        await self.fill_missing_sectors()
        if self.manual_sources:
            await self.complete_info()
        await self.refresh_margins()

    async def fill_missing_sectors(self) -> int:
        """Sector (IBKR) de los tickers de la watchlist que aún no lo tienen, p. ej. por actualizarse sin TWS."""
        watch = set(self.watchlist.list())
        todo = [t for t, i in self.ticker_info.all().items() if i.sector is None and t in watch]
        done, failures = 0, 0
        for ticker in todo:
            if failures >= 3:
                break
            try:
                sector, category = await asyncio.wait_for(self.gateway.get_sector_info(ticker), 15)
            except BrokerDisconnectedError:
                break
            except Exception:   # ticker desconocido, tiempo agotado...
                failures += 1
                continue
            if sector:
                self.ticker_info.update_sector(ticker, sector, category)
                done += 1
        if done:
            self.state.data_version += 1
        return done

    async def refresh_margins(self) -> bool:
        """Pide a TWS los márgenes (what-if) que faltan o caducaron de los contratos en alcance, sin volver a
        cotizar nada. Espera su turno si hay otra ejecución en curso."""
        async with self._lock:
            try:
                self.state.activity = "Pidiendo los márgenes pendientes a TWS"
                await self.refresh_job.fill_margins()
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            finally:
                self.state.activity = None
            self.state.data_version += 1
            return True

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
            self._load_portfolio()
            self._load_vix()
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

    def filing_alert_map(self) -> dict[str, list[FilingAlert]]:
        """Alertas vigentes de la SEC por ticker normalizado (`norm_ticker`): solo los que tienen alguna dentro de la ventana configurada."""
        today, window = self.now().date(), self.settings.edgar.alerts_window_days
        out: dict[str, list[FilingAlert]] = {}
        for ticker, (_, alerts) in self.filing_alerts.all().items():
            recent = [a for a in alerts if 0 <= (today - a.day).days <= window]
            if recent:
                out[norm_ticker(ticker)] = recent
        return out

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
            "filing_alerts": self.filing_alerts.purge_except(self.quality_scope()),
        }
        if any(removed.values()):
            log.info("Limpieza de tickers fuera de la watchlist: %s", removed)
        return removed

    def launch(self, coro) -> "asyncio.Future":
        """Ejecuta una corrutina en segundo plano (acciones manuales largas)."""
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

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
        if report is not None and report.updated and (self.state.connected or self._offline_capable()):
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
            if self._tasty_running():
                self._tasty_again = True   # la descarga de arranque sigue (no se espera con el bloqueo tomado): repetirá al acabar
            elif report.updated or not self._tasty_done:   # sin nada nuevo, el arranque ya descargó histórico y calidad
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
        await self._update_iv_stats()

    async def _update_iv_stats(self) -> None:
        """IV Rank/Percentile, HV30, IV30 y cambio de IV de la watchlist, aunque el mercado esté cerrado."""
        try:
            if self.daily.iv_stats_due() and await self.daily.update_iv_stats(self.watchlist.list()):
                self.state.data_version += 1
        except Exception:
            log.exception("No se pudieron actualizar la IV y la volatilidad histórica")

    def update_iv_stats_if_due(self) -> None:
        """Con la app encendida de forma continua, baja la IV y la volatilidad histórica en cuanto pasa el recálculo diario de
        tastytrade (o si faltan datos), sin esperar a un refresco completo de mercado."""
        if self._iv_stats_running or not self.daily.iv_stats_due():
            return
        self._iv_stats_running = True

        async def run() -> None:
            try:
                await self._update_iv_stats()
            finally:
                self._iv_stats_running = False

        self.launch(run())

    async def _update_fundamentals(self) -> None:
        """Datos de calidad de toda la watchlist (EPS, trimestres, capitalización, liquidez, resultados). Un fallo
        no interrumpe nada: los filtros de calidad trabajan con lo guardado."""
        try:
            await self.daily.update_fundamentals(self.quality_scope())
        except Exception:
            log.exception("No se pudieron actualizar los datos de calidad")
        finally:
            self.state.data_version += 1

    def update_quality_if_due(self) -> None:
        """Con la app encendida de forma continua, baja los datos de calidad en cuanto pasa el recálculo diario de las
        market metrics de tastytrade (`scanner.quality.metrics_ready_utc`), sin esperar al siguiente arranque."""
        if self.daily.fundamentals is None or self._quality_running or not self.daily.metrics_due():
            return
        self._quality_running = True

        async def run() -> None:
            try:
                await self._update_fundamentals()
            finally:
                self._quality_running = False

        self.launch(run())

    def update_financials_in_background(self) -> None:
        """Lanza la consulta a SEC EDGAR (la primera vez dura minutos) sin esperar y sin solaparse consigo misma."""
        if self.daily.financials is None or self._financials_running:
            return
        self._financials_running = True

        async def run() -> None:
            try:
                changed = await self.daily.update_financials(self.quality_scope())
                scope = sorted({*self.quality_scope(), *(p.ticker for p in self.state.positions)})
                if await self.daily.update_filing_alerts(scope) or changed:
                    self.state.data_version += 1
            except Exception:
                log.exception("No se pudieron actualizar el balance y el flujo de caja")
            finally:
                self._financials_running = False

        self.launch(run())

    def _set_scanner_config(self, presets: tuple[ScannerPreset, ...], strategy: StrategySettings,
                            spreads: Optional[SpreadSettings] = None) -> None:
        """Sustituye los presets, la estrategia y los valores del modo Spread en los ajustes que comparten el servicio y los jobs (son `frozen`, así que se
        escribe en el objeto directamente): todo lo que los lee (botones, refresco, aviso del VIX) los ve en la siguiente llamada."""
        object.__setattr__(self.settings.scanner, "presets", presets)
        object.__setattr__(self.settings.scanner, "strategy", strategy)
        if spreads is not None:
            object.__setattr__(self.settings.scanner, "spreads", spreads)

    def _load_scanner_config(self) -> None:
        raw = self.meta.get(SCANNER_USER_CONFIG)
        if not raw:
            return
        def known(model, values: dict) -> dict:
            """Solo las claves que el modelo actual aún tiene: un campo retirado (p. ej. un filtro que se quitó) no invalida lo guardado."""
            gone = sorted(set(values) - set(model.model_fields))
            if gone:
                log.info("Configuración del Scanner guardada: se ignoran los campos retirados de %s: %s", model.__name__, ", ".join(gone))
            return {k: v for k, v in values.items() if k in model.model_fields}

        try:
            data = json.loads(raw)
            spreads = SpreadSettings(**known(SpreadSettings, data["spreads"])) if data.get("spreads") else None   # las guardadas antes no lo traen
            self._set_scanner_config(tuple(ScannerPreset(**known(ScannerPreset, p)) for p in data["presets"]),
                                     StrategySettings(**known(StrategySettings, data["strategy"])), spreads)
        except Exception:   # una configuración guardada que ya no es válida (cambió el modelo): se usa la de config.yaml
            log.exception("Configuración del Scanner guardada no válida; se usa la de config.yaml")

    def save_scanner_config(self, presets: list[dict], strategy: dict, spreads: Optional[dict] = None) -> None:
        """Guarda y aplica los presets (Mensual/Táctico…), los valores de la estrategia de venta de puts y los del modo Spread editados en la
        aplicación; sobreviven a reinicios y mandan sobre config.yaml hasta `reset_scanner_config`. Lanza `ValidationError`/
        `ValueError` si no son válidos (no se guarda nada)."""
        self._scan_memo.clear()      # los criterios por defecto y el exento de sectores pueden cambiar
        new_presets = tuple(ScannerPreset(**p) for p in presets)
        if not new_presets:
            raise ValueError("Tiene que quedar al menos una configuración")
        new_strategy = StrategySettings(**strategy)
        new_spreads = SpreadSettings(**spreads) if spreads is not None else self.settings.scanner.spreads
        self.meta.set(SCANNER_USER_CONFIG, json.dumps({"presets": [p.model_dump() for p in new_presets], "strategy": new_strategy.model_dump(),
                                                       "spreads": new_spreads.model_dump()}))
        self._set_scanner_config(new_presets, new_strategy, new_spreads)

    def stored_profiles(self) -> Optional[tuple[list[ScannerProfile], str]]:
        """(perfiles, perfil inicial) guardados desde la aplicación, o None si se usan los de config.yaml."""
        return profiles_mod.load(self.meta.get(SCANNER_PROFILES) or "")

    def save_profiles(self, profiles: list[ScannerProfile], default: Optional[str]) -> None:
        """Guarda los perfiles del Scanner (sobreviven a reinicios). Lanza `ValueError` si no son válidos (no se guarda nada)."""
        valid, default_name = profiles_mod.validate_profiles(profiles, default)
        self.meta.set(SCANNER_PROFILES, profiles_mod.dump(valid, default_name))

    def reset_profiles(self) -> None:
        """Vuelve a los perfiles que salen de los presets TPC y de la estrategia (config.yaml o editados en /scanner/config)."""
        self.meta.set(SCANNER_PROFILES, "")

    def reset_scanner_config(self) -> None:
        """Vuelve a los presets y la estrategia de config.yaml."""
        self._scan_memo.clear()      # los criterios por defecto y el exento de sectores pueden cambiar
        self.meta.set(SCANNER_USER_CONFIG, "")
        self._set_scanner_config(*self._yaml_scanner)

    # ---- Fuentes aptas para spreads --------------------------------------------------------------
    def spread_source_names(self) -> set[str]:
        """Fuentes del Universo aptas para spreads: la del radar de Tortuga (`tortuga.source_name`, siempre) y las que el usuario marcó."""
        try:
            marked = json.loads(self.meta.get(SPREAD_SOURCES) or "[]")
        except (ValueError, TypeError):
            marked = []
        return {self.settings.tortuga.source_name, *(n for n in marked if isinstance(n, str))}

    def set_spread_source(self, name: str, on: bool) -> None:
        """Marca o desmarca una fuente del Universo como apta para spreads (la del radar de Tortuga no se puede desmarcar)."""
        if name not in {s.name for s in self.universe_sources}:
            raise WatchlistError(f"La fuente «{name}» no existe")
        marked = self.spread_source_names() - {self.settings.tortuga.source_name}
        marked = marked | {name} if on else marked - {name}
        self.meta.set(SPREAD_SOURCES, json.dumps(sorted(marked)))
        self.state.data_version += 1

    def spread_only_tickers(self) -> frozenset:
        """Tickers que SOLO están en fuentes aptas para spreads: no pasan filtros de la empresa y solo se escanean como spread."""
        def build() -> frozenset:
            flagged = self.spread_source_names()
            return frozenset(t for t, names in self.ticker_lists().items() if names and all(n in flagged for n in names))
        return self._memo("spread_only", build)

    def quality_exempt(self) -> tuple[str, ...]:
        """Sectores y tickers exentos de los filtros de la empresa: los de `scanner.quality` más los ETF del radar de Tortuga (sector «ETF»)."""
        base = self.settings.scanner.quality.exempt_entries
        radar = self.radar_spreads()
        etfs = sorted({r.ticker for r in radar.rows if (r.sector or "").upper() == "ETF"}) if radar else []
        return (*base, *(f"={t}" for t in etfs))

    def remove_legacy_spread_universe(self) -> bool:
        """Migración única: quita la fuente «Universo_BPS» del antiguo universo para spreads (lo sustituye el radar de Tortuga) y su marca
        de volcado. Los tickers que ya estén en la watchlist no se tocan. True si había algo que quitar."""
        legacy = "Universo_BPS"
        changed = False
        if legacy in self.manual_sources:
            del self.manual_sources[legacy]
            self._save_manual()
            changed = True
        if self.meta.get(SPREAD_UNIVERSE_SYNCED):
            self.meta.set(SPREAD_UNIVERSE_SYNCED, "")
        try:
            marked = json.loads(self.meta.get(SPREAD_SOURCES) or "[]")
        except (ValueError, TypeError):
            marked = []
        if legacy in marked:
            self.meta.set(SPREAD_SOURCES, json.dumps(sorted(n for n in marked if n != legacy)))
            changed = True
        if changed:
            self._scan_memo.clear()
            log.info("Quitada la fuente «%s» del antiguo universo para spreads (la sustituye el radar de Tortuga)", legacy)
        return changed

    # ---- Tortuga: rutinas que lanza la propia aplicación y radar de Bull Put Spreads -------------------
    def tortuga_in_background(self, force: Optional[str] = None) -> bool:
        """Lanza sin esperar las rutinas de Tortuga que toquen hoy (o solo `force`, a mano) una detrás de otra. False si no hay nada que hacer."""
        runner = self.tortuga
        if runner is None or not self.settings.tortuga.enabled or self._tortuga_busy:
            return False
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        todo = (list(TORTUGA_ROUTINES.values()) if force == "all" else [TORTUGA_ROUTINES[force]]) if force else runner.due()
        if not todo:
            return False
        self._tortuga_busy = True

        async def run() -> None:
            try:
                for routine in todo:
                    await runner.run_one(routine.key)
                self.state.data_version += 1
                self.sync_community_in_background()
                self.sync_radar_in_background()
            except Exception:
                log.exception("Falló una rutina de Tortuga")
            finally:
                self._tortuga_busy = False

        self.launch(run())
        return True

    def radar_spreads(self) -> Optional[RadarData]:
        """Radar de Bull Put Spreads (JSON de la rutina), releído solo si el fichero cambió; si el nuevo es inválido se conserva el último bueno."""
        path = Path(self.settings.tortuga.radar_path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._radar = None
            return None
        if self._radar is not None and self._radar[0] == mtime:
            return self._radar[1]
        try:
            data = load_radar(path)
        except RadarError as exc:
            if self._radar_error != str(exc):
                log.warning("Radar de Tortuga: %s no es válido (%s); se conservan los últimos datos buenos", path, exc)
                self._radar_error = str(exc)
            return self._radar[1] if self._radar is not None else None
        self._radar, self._radar_error = (mtime, data), None
        return data

    def radar_min_yield(self) -> float:
        """Yield mínimo del periodo (% de la prima sobre el strike) que exige el perfil `tortuga.profile` a las puts; si el perfil no lo
        fija, el de la estrategia."""
        name = self.settings.tortuga.profile
        for prof in self.scanner_profiles():
            if prof.name == name or {name, prof.name} == {"Tastytrade", "TastyPut"}:
                value = _to_float(prof.put.get("min_yield"))
                if value is not None:
                    return value
        return self.settings.scanner.strategy.min_yield_pct

    def radar_passing(self) -> list[str]:
        data = self.radar_spreads()
        if data is None:
            return []
        minimum = self.radar_min_yield()
        return [r.ticker for r in data.rows if radar_passes(r, minimum)]

    def sync_radar_in_background(self) -> None:
        if self._radar_syncing or self.radar_spreads() is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._radar_syncing = True

        async def run() -> None:
            try:
                await self.sync_radar_source()
            except Exception:
                log.exception("No se pudo actualizar la fuente del radar de Tortuga")
            finally:
                self._radar_syncing = False

        self.launch(run())

    async def sync_radar_source(self) -> Optional[dict]:
        """La fuente del Universo `tortuga.source_name` pasa a ser los valores del radar cuya put de 30Δ rinde en el periodo al menos el
        yield del perfil, y esos tickers se añaden a la watchlist (con su actualización diaria en segundo plano). Solo AÑADE a la
        watchlist: nunca quita nada. No hace nada si ese JSON (con ese umbral) ya se volcó."""
        cfg = self.settings.tortuga
        data = self.radar_spreads()
        if data is None:
            return None
        stamp = f"{data.generated_at.isoformat()}|{self.radar_min_yield():g}"
        if self.meta.get(RADAR_SYNCED) == stamp and cfg.source_name in self.manual_sources:
            return None
        parsed = parse_tokens(self.radar_passing())
        if not parsed.tickers:
            log.info("Radar de Tortuga: ningún valor llega al yield del perfil «%s»; la fuente «%s» no cambia", cfg.profile, cfg.source_name)
            return None
        try:
            result = await self.add_manual_tickers(parsed, cfg.source_name, replace_source=True)
        except WatchlistError as exc:
            log.warning("Radar de Tortuga: no se pudo actualizar la fuente «%s»: %s", cfg.source_name, exc)
            return None
        if result["added"]:
            new = await self.add_watchlist(parse_tokens(list(result["added"])), background=True)
            result["watchlist_new"] = new
        self.meta.set(RADAR_SYNCED, stamp)
        self.state.data_version += 1
        return result

    def community(self) -> Optional[CommunityData]:
        """Datos de la comunidad de Tortuga (JSON de la rutina diaria), releídos solo si el fichero cambió. None si no existe; si el nuevo
        es inválido se conservan los últimos buenos (y se avisa una vez en el log)."""
        path = Path(self.settings.community.path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._community = None
            return None
        if self._community is not None and self._community[0] == mtime:
            return self._community[1]
        try:
            data = load_community(path)
        except CommunityError as exc:
            if self._community_error != str(exc):
                log.warning("Comunidad: %s no es válido (%s); se conservan los últimos datos buenos", path, exc)
                self._community_error = str(exc)
            return self._community[1] if self._community is not None else None
        self._community, self._community_error = (mtime, data), None
        for w in data.warnings:
            log.warning("Comunidad: %s", w)
        return data

    def community_counts(self) -> dict[str, tuple[int, int]]:
        """{ticker: (traders en 30 días, traders en 7 días)} que han vendido puts o spreads de puts."""
        data = self.community()
        return {t.ticker: (t.month_traders, t.week_traders) for t in data.tickers} if data is not None else {}

    def community_ideas(self) -> list[IdeaView]:
        """Las ideas del JSON de la comunidad comparadas con los datos de hoy (precio, cotización de las patas, cierre del día de la apertura)."""
        data = self.community()
        if data is None or not data.ideas:
            return []
        tickers = {i.ticker for i in data.ideas}
        watchlist = set(self.watchlist.list())
        snapshots = {snapshot_key(s.contract.ticker, s.contract.expiry, s.contract.strike): s
                     for t in sorted(tickers & watchlist) for s in self.snapshots.all(t) if s.contract.right is OptionRight.PUT}
        infos, closes = self.ticker_info.all(), self.bars.all_closes(tickers & watchlist)
        live = self._community_live
        if live is not None and live[1] == data.generated_at.isoformat():
            for key, snap in live[2].items():
                snapshots.setdefault(key, snap)
            for t, info in live[3].items():
                if infos.get(t) is None or infos[t].underlying_price is None:
                    infos[t] = info
            for t, bars in live[4].items():
                closes.setdefault(t, bars)
        ref = self.criteria()
        views = evaluate_ideas(data.ideas, self.now().date(), snapshots, infos, closes, watchlist, ref.price_reference, ref.price_spread_pct)
        sc = self.settings.scanner
        prices = {}       # rango de precio del subyacente que cada perfil (por nombre) tiene en su juego de filtros de put
        for prof in self.scanner_profiles():
            lo, hi = (_to_float(prof.put.get(k)) for k in ("price_min", "price_max"))
            if lo is not None or hi is not None:
                prices[prof.name] = (lo, hi)
        closes = self.openings().closes if self.openings() is not None else ()
        days, today = self.settings.community.closed_marker_days, self.now().date()
        return [replace(v, fits=strategy_fits(v, sc.presets, sc.strategy, prices), traded=past_trade(v.idea, closes, today, days)) for v in views]

    async def refresh_community_quotes(self, max_age_seconds: Optional[float] = 300) -> None:
        """Cotiza en directo (tastytrade) las patas de las ideas que no están guardadas (tickers fuera de la watchlist, strikes o
        vencimientos fuera del catálogo), más el precio y los cierres recientes de esos tickers, para poder valorarlas. Se guarda solo
        en memoria. Con `max_age_seconds=None` solo repite si el JSON es otro. Un fallo del proveedor deja las ideas «sin valorar»."""
        data = self.community()
        provider = self.volatility
        if (data is None or self._community_live_running or provider is None
                or not all(hasattr(provider, m) for m in ("get_put_listing", "get_option_quotes", "get_prices", "get_daily_closes", "get_iv_metrics"))):
            return
        stamp, now = data.generated_at.isoformat(), self.now()
        live = self._community_live
        if live is not None and live[1] == stamp and (
                max_age_seconds is None or (now - live[0]).total_seconds() < max_age_seconds
                or (self._paused() and live[0].astimezone() >= self.market.last_close(now))):    # mercado cerrado y ya cotizadas tras el cierre: no cambian
            return
        today = now.date()
        watchlist = set(self.watchlist.list())
        stored = {snapshot_key(s.contract.ticker, s.contract.expiry, s.contract.strike)
                  for t in {i.ticker for i in data.ideas} & watchlist for s in self.snapshots.all(t) if s.contract.right is OptionRight.PUT}
        legs: dict[str, set[tuple[date, float]]] = {}
        for idea in data.ideas:
            if idea.expiry < today:
                continue
            for strike in (idea.short_strike, idea.long_strike):
                if strike is not None and snapshot_key(idea.ticker, idea.expiry, strike) not in stored:
                    legs.setdefault(idea.ticker, set()).add((idea.expiry, round(float(strike), 4)))
        if not legs:
            self._community_live = (now, stamp, {}, {}, {})
            return
        self._community_live_running = True
        try:
            sem = asyncio.Semaphore(4)

            async def listing(ticker: str) -> dict:
                async with sem:
                    try:
                        return await provider.get_put_listing(ticker)
                    except Exception as exc:
                        log.info("Ideas: sin cadena de %s (%s)", ticker, exc)
                        return {}

            listings = dict(zip(legs, await asyncio.gather(*(listing(t) for t in legs))))
            symbols: dict[str, tuple[str, date, float]] = {}
            for ticker, wanted in legs.items():
                for expiry, strike in wanted:
                    sym = listings[ticker].get((expiry, round(strike, 2)))
                    if sym:
                        symbols[sym] = (ticker, expiry, strike)
            snaps: dict = {}
            try:
                quotes = await provider.get_option_quotes(list(symbols)) if symbols else {}
            except Exception as exc:
                log.warning("Ideas: no se pudieron cotizar las patas: %s", exc)
                quotes = {}
            for sym, q in quotes.items():
                ticker, expiry, strike = symbols[sym]
                snaps[snapshot_key(ticker, expiry, strike)] = ContractSnapshot(
                    OptionContract(ticker, expiry, strike), now, q.bid, q.ask, q.last, q.delta, q.iv, q.open_interest, bid_size=q.bid_size)
            tickers = [t for t in legs if t not in watchlist]
            infos: dict[str, TickerInfo] = {}
            closes: dict = {}
            if tickers:
                try:
                    infos = {t: TickerInfo(t, underlying_price=p, price_at=now) for t, p in (await provider.get_prices(tickers)).items()}
                except Exception as exc:
                    log.warning("Ideas: no se pudieron obtener los precios: %s", exc)
                try:
                    for t, m in (await provider.get_iv_metrics(tickers)).items():
                        infos[t] = replace(infos.get(t) or TickerInfo(t), iv_rank=m.iv_rank, iv_percentile=m.iv_percentile)
                except Exception as exc:
                    log.warning("Ideas: no se pudo obtener el IV Rank: %s", exc)
                oldest = min((i.day for i in data.ideas if i.ticker in tickers), default=today)
                try:
                    closes = await provider.get_daily_closes(tickers, (today - oldest).days + 7)
                except Exception as exc:
                    log.warning("Ideas: no se pudieron obtener los cierres: %s", exc)
            self._community_live = (now, stamp, snaps, infos, closes)
            self.state.data_version += 1
        finally:
            self._community_live_running = False

    def community_stale(self) -> Optional[float]:
        """Horas desde que se generó el JSON si superan `community.stale_hours` (la rutina no ha corrido); None si está al día o no hay."""
        data = self.community()
        if data is None:
            return None
        now = self.now()
        generated = data.generated_at if data.generated_at.tzinfo is None or now.tzinfo is not None else data.generated_at.replace(tzinfo=None)
        try:
            hours = (now - generated).total_seconds() / 3600
        except TypeError:       # uno con zona horaria y el otro sin ella
            hours = (now.replace(tzinfo=None) - generated.replace(tzinfo=None)).total_seconds() / 3600
        return hours if hours > self.settings.community.stale_hours else None

    def sync_community_in_background(self) -> None:
        """Vuelca los tickers de la comunidad en su fuente del Universo si hay un JSON nuevo, sin esperar y sin solaparse."""
        if self._community_syncing or self.community() is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._community_syncing = True

        async def run() -> None:
            try:
                await self.sync_community_source()
            except Exception:
                log.exception("No se pudo actualizar la fuente de la comunidad")
            finally:
                self._community_syncing = False

        self.launch(run())

    async def sync_community_source(self) -> Optional[dict]:
        """La fuente del Universo (`community.source_name`) pasa a ser los tickers con más traders vendiendo puts en 30 días
        (`min_traders`, `universe_top`). No hace nada si ese JSON ya se volcó. Devuelve el resultado de `add_manual_tickers`."""
        cfg = self.settings.community
        data = self.community()
        if data is None:
            return None
        stamp = data.generated_at.isoformat()
        if self.meta.get(COMMUNITY_SYNCED) == stamp and cfg.source_name in self.manual_sources:
            return None
        top = sorted((t for t in data.tickers if t.month_traders >= cfg.min_traders),
                     key=lambda t: (-t.month_traders, -t.month_operations, t.ticker))[:cfg.universe_top]
        parsed = parse_tokens([t.ticker for t in top])
        if not parsed.tickers:
            log.info("Comunidad: ningún ticker llega a %d traders; la fuente «%s» no cambia", cfg.min_traders, cfg.source_name)
            return None
        try:
            result = await self.add_manual_tickers(parsed, cfg.source_name, replace_source=True)
        except WatchlistError as exc:
            log.warning("Comunidad: no se pudo actualizar la fuente «%s»: %s", cfg.source_name, exc)
            return None
        self.meta.set(COMMUNITY_SYNCED, stamp)
        self.state.data_version += 1
        return result

    def scanner_profiles(self) -> list[ScannerProfile]:
        """Perfiles del Scanner (los que da la interfaz web o, si no hay, los guardados)."""
        if self.profile_provider is not None:
            return self.profile_provider()[0]
        stored = self.stored_profiles()
        return stored[0] if stored else []

    def put_profiles(self) -> dict[str, str]:
        """Perfil del Scanner elegido a mano para cada put vendida (corrige la clasificación automática), por `portfolio.management.put_key`.
        Los valores antiguos («tastytrade» / «tpc», que eran el estilo de gestión) se traducen al primer perfil con ese estilo."""
        try:
            data = json.loads(self.meta.get(PUT_PROFILES) or "{}")
        except ValueError:
            return {}
        profiles = self.scanner_profiles()
        names = {p.name for p in profiles}
        out: dict[str, str] = {}
        for k, v in data.items():
            if v in names:
                out[str(k)] = str(v)
            elif v in PUT_PROFILE_NAMES:
                legacy = next((p.name for p in profiles if p.management == v and p.name != FALLBACK_PROFILE), None)
                if legacy:
                    out[str(k)] = legacy
        return out

    def set_put_profile(self, key: str, profile: str) -> None:
        """Recuerda el perfil del Scanner de una put (`TICKER|AAAA-MM-DD|strike`). Olvida las ya vencidas. `ValueError` si no es válido."""
        parts = key.split("|")
        if profile not in {p.name for p in self.scanner_profiles()} or len(parts) != 3 or not parts[0]:
            raise ValueError("perfil no válido")
        date.fromisoformat(parts[1])
        today = self.now().date().isoformat()
        data = {k: v for k, v in {**self.put_profiles(), key: profile}.items() if k.split("|")[1] >= today}
        self.meta.set(PUT_PROFILES, json.dumps(data))

    def openings(self) -> Optional[OpeningsData]:
        """Fechas exactas de apertura (JSON de la rutina de aperturas), releídas solo si el fichero cambió. None si no existe; si el nuevo es
        inválido se conservan los últimos datos buenos."""
        path = Path(self.settings.community.openings_path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._openings = None
            return None
        if self._openings is not None and self._openings[0] == mtime:
            return self._openings[1]
        try:
            data = load_openings(path)
        except OpeningsError as exc:
            if self._openings_error != str(exc):
                log.warning("Aperturas: %s no es válido (%s); se conservan los últimos datos buenos", path, exc)
                self._openings_error = str(exc)
            return self._openings[1] if self._openings is not None else None
        self._openings, self._openings_error = (mtime, data), None
        for w in data.warnings:
            log.warning("Aperturas: %s", w)
        return data

    def _short_puts_info(self) -> list[tuple[str, date, str, float]]:
        """(put_key, vencimiento, ticker, strike) de cada put vendida de la cartera."""
        return [(put_key_of(p), p.option.expiry, p.ticker, p.option.strike) for p in self.state.positions
                if p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0]

    def _short_put_keys(self) -> list[tuple[str, date]]:
        return [(key, expiry) for key, expiry, _, _ in self._short_puts_info()]

    def _put_opened(self) -> dict[str, str]:
        try:
            return {str(k): str(v) for k, v in json.loads(self.meta.get(PUT_OPENED) or "{}").items()}
        except ValueError:
            return {}

    def _note_put_opened(self) -> None:
        """Apunta el día en que aparece por primera vez cada put vendida (IBKR no da la fecha de apertura); olvida las vencidas."""
        today = self.now().date().isoformat()
        known = self._put_opened()
        data = {k: v for k, v in known.items() if k.split("|")[1] >= today}
        for key, _ in self._short_put_keys():
            data.setdefault(key, today)
        if data != known:
            self.meta.set(PUT_OPENED, json.dumps(data))

    def put_assignments(self) -> dict[str, str]:
        """Perfil del Scanner de cada put vendida: el elegido a mano o, si no, el que corresponde al DTE con el que se abrió (fecha en que la
        app la vio por primera vez); lo que no encaja en ningún perfil va a «Tortuga»."""
        profiles = self.scanner_profiles()
        if not profiles:
            return {}
        manual = self.put_profiles()
        dates = self.put_open_dates()
        out: dict[str, str] = {}
        for key, expiry in self._short_put_keys():
            if key in manual:
                out[key] = manual[key]
                continue
            out[key] = classify_dte((expiry - dates[key][0]).days, profiles)
        return out

    def put_open_dates(self) -> dict[str, tuple[date, bool]]:
        """{put_key: (fecha de apertura, es exacta)}: la de las operaciones de Tortuga si la rutina de aperturas la trae y, si no (Tortuga las publica
        con un día de retraso), la primera vez que la app vio la posición."""
        exact, seen, today = self.openings(), self._put_opened(), self.now().date()
        out: dict[str, tuple[date, bool]] = {}
        for key, expiry, ticker, strike in self._short_puts_info():
            tortuga = exact.get(ticker, expiry, strike) if exact is not None else None
            if tortuga is not None:
                out[key] = (tortuga, True)
            else:
                out[key] = (date.fromisoformat(seen[key]) if key in seen else today, False)
        return out

    def put_management(self) -> dict[str, str]:
        """Estilo de gestión (tastytrade | tpc) de cada put vendida, según el perfil que tiene asignado."""
        style = {p.name: p.management for p in self.scanner_profiles()}
        return {key: style.get(name, "tastytrade") for key, name in self.put_assignments().items()}

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

    def infos(self, criteria: Optional[ScanCriteria] = None) -> dict[str, TickerInfo]:
        """Fichas de los tickers. Si el filtro IV/HV está activo, a las que no tienen HV30 (tastytrade no la calcula para algunos
        valores) se les pone la calculada con los cierres diarios guardados. No se guarda: se recalcula al consultar."""
        infos = self.ticker_info.all()
        if criteria is None or criteria.min_iv_hv_ratio is None:
            return infos
        missing = [t for t, i in infos.items() if not i.hv30]
        if missing:
            for t, hv in self.bars.hv(missing, self.settings.scanner.technical.hv_period).items():
                infos[t] = replace(infos[t], hv30=hv)
        return infos

    def filter_data_gaps(self, criteria: ScanCriteria) -> list[str]:
        """Avisos de los filtros activos a los que les faltan datos en parte de la watchlist: sus contratos se descartan, y sin el
        aviso un resultado vacío no se explicaría."""
        tickers = self.watchlist.list()
        total = len(tickers)
        if not total:
            return []
        c = criteria
        infos = self.infos(c)
        checks = []   # (qué falta, cuántos, qué pasa con ellos, cómo se rellena)
        if c.min_iv_hv_ratio is not None:
            checks.append(("IV/HV30", sum(1 for t in tickers if not iv_hv_available(infos.get(t))), KEPT,
                           "tastytrade no da la IV30/HV30 de algunos valores; se sustituyen con el índice de IV y los cierres diarios, y falta si tampoco hay esos"))
        if c.max_iv_change_5d is not None:
            checks.append(("el cambio de IV a 5 días", sum(1 for t in tickers if not iv_change_available(infos.get(t))), KEPT,
                           "tastytrade no lo da para algunos valores"))
        if c.min_iv_rank is not None:
            checks.append(("el IV Rank", sum(1 for t in tickers if not infos.get(t) or infos[t].iv_rank is None), KEPT,
                           "Se rellenan con la actualización diaria"))
        if c.min_atr_multiple is not None:
            have = self.bars.atr(tickers, self.settings.scanner.technical.atr_period)
            checks.append(("el ATR", sum(1 for t in tickers if t not in have), KEPT,
                           "Hacen falta los máximos y mínimos diarios, que se descargan con el histórico (si tastytrade no responde, se reintenta en la siguiente actualización)"))
        return [f"Faltan datos para {label} en {n} de {total} tickers: {effect}. {hint}."
                for label, n, effect, hint in checks if n > 0]

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
        self.update_iv_stats_if_due()            # ídem para IV Rank/Percentile, HV30 y cambio de IV (también con el mercado cerrado)
        self.update_quality_if_due()             # ídem: solo si tastytrade ha recalculado sus métricas desde la última descarga
        self.sync_community_in_background()      # ídem: solo si la rutina diaria dejó un JSON nuevo de la comunidad
        self.tortuga_in_background()             # lanza las rutinas de Tortuga que toquen hoy (CLI de Claude, solo lectura)
        self.sync_radar_in_background()          # ídem con el radar de Bull Put Spreads: fuente TortugaSpreads y watchlist
        if not self._paused() or self._capture_needed():
            return await self.refresh_all()
        log.info("Mercado cerrado: se refrescan solo cartera y VIX")
        return await self.refresh_all(include_market=False)

    async def refresh_all(self, include_market: bool = True) -> bool:
        """Cuenta, riesgo y VIX más los contratos (`include_market=False`: solo cuenta, riesgo y VIX, sin
        cotizaciones de subyacentes, opciones ni márgenes). La cuenta se refresca a la vez que el mercado y con
        su propio bloqueo. False si se omitió por solapamiento o falta de conexión."""
        if self._known_disconnected() and not self._offline_capable():
            return False
        if not include_market:
            ok = await self.refresh_account(skip_closed_vix=True)
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

    async def refresh_account(self, skip_closed_vix: bool = False) -> bool:
        """Cuenta, posiciones, riesgo y VIX. Va por su cuenta (bloqueo propio): no espera a la actualización diaria
        ni a un ciclo largo de cotizaciones. False si ya hay uno en curso o se perdió la conexión."""
        if self._account_lock.locked():
            return False
        offline = self._known_disconnected()
        if offline and not self._offline_capable():
            return False
        async with self._account_lock:
            try:
                vix = self._refresh_vix(skip_closed=skip_closed_vix)
                if offline:   # sin TWS solo el VIX (tastytrade); cuenta y posiciones se quedan como estaban
                    await vix
                else:
                    await asyncio.gather(self._refresh_portfolio(), vix)
            except BrokerDisconnectedError as exc:
                self._disconnected(exc)
                return False
            self.state.last_account_refresh = self.now()
            return True

    async def refresh_new_contracts(self) -> bool:
        """Cotiza (datos congelados del último cierre) solo los contratos guardados que nunca se han
        cotizado y encajan con el filtro inicial. No toca el marcador del último refresco completo."""
        if self._known_disconnected() and not self._offline_capable():
            return False
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

    def _known_disconnected(self) -> bool:
        """Sin conexión con el broker (error de conexión registrado): no se lanzan refrescos, que irían a tastytrade
        (VIX, precios, IV) para fallar después en IBKR. El bucle de reconexión los reanuda al conectar."""
        return "connection" in self.state.errors

    def _disconnected(self, exc: Exception) -> None:
        self.state.connected = False
        self.state.errors["connection"] = str(exc)
        log.warning("Conexión perdida: %s", exc)

    def _load_vix(self) -> None:
        """Recupera el último VIX guardado: con el mercado cerrado y ya posterior al último cierre no se vuelve a pedir al arrancar."""
        if self.state.vix is None:
            self.state.vix = vix_cache.load(self.meta.get(VIX_CACHE))

    def _load_portfolio(self) -> None:
        """Recupera la última cuenta y posiciones leídas de TWS (de este modo de cuenta) para mostrarlas sin conexión."""
        account, positions = portfolio_cache.load(self.meta.get(PORTFOLIO_CACHE), self.settings.ibkr.mode.value)
        if account is not None:
            self.state.account, self.state.positions = account, positions
            self.state.risk = build_risk_status(account, self.settings.risk.cushion_thresholds)

    async def _refresh_portfolio(self) -> None:
        try:
            self.state.account = await asyncio.wait_for(self.gateway.get_account_summary(), timeout=STEP_TIMEOUT_SECONDS)
            self.state.positions = await asyncio.wait_for(self.gateway.get_positions(), timeout=STEP_TIMEOUT_SECONDS)
            self._note_put_opened()
            self.state.risk = build_risk_status(self.state.account, self.settings.risk.cushion_thresholds)
            self.state.errors.pop("portfolio", None)
            self.meta.set(PORTFOLIO_CACHE, portfolio_cache.dump(
                self.settings.ibkr.mode.value, self.state.account, self.state.positions))
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            self.state.errors["portfolio"] = str(exc)
        except asyncio.TimeoutError:
            self.state.errors["portfolio"] = "Timeout obteniendo la cuenta (¿TWS ha perdido la conexión con IBKR?)"

    def _vix_is_current(self) -> bool:
        """Con el mercado cerrado, ¿el VIX en memoria ya es posterior al último cierre? Entonces no puede haber cambiado."""
        vix = self.state.vix
        if vix is None or vix.updated_at is None or not self._paused():
            return False
        return vix.updated_at.astimezone() >= self.market.last_close(self.now())

    async def _refresh_vix(self, skip_closed: bool = False) -> None:
        if skip_closed and self._vix_is_current():
            return   # mercado cerrado y ya se capturó el cierre: se ahorran las conexiones a tastytrade
        try:
            self.state.vix = await asyncio.wait_for(
                self.gateway.get_vix_data(self.settings.vix.history_days, self.settings.vix.futures_ahead),
                timeout=STEP_TIMEOUT_SECONDS,
            )
            self.state.errors.pop("vix", None)
            self.meta.set(VIX_CACHE, vix_cache.dump(self.state.vix))
        except BrokerDisconnectedError:
            raise
        except BrokerError as exc:
            self.state.errors["vix"] = str(exc)
        except asyncio.TimeoutError:
            self.state.errors["vix"] = "Timeout obteniendo el VIX"

    # ---- casos de uso de lectura -------------------------------------------------------------
    def criteria(self, **overrides) -> ScanCriteria:
        return criteria_from_settings(self.settings).with_filters(**overrides)

    def _memo(self, name: str, build):
        """Lectura costosa (snapshots, cierres, ATR) reutilizada entre los cálculos de una misma petición del Scanner: «Ambos» escanea dos
        veces y el aviso de resultados vacíos una más. Caduca a los `SCAN_MEMO_SECONDS` o si cambian los datos (`data_version`)."""
        now = time.monotonic()
        key = (name, self.state.data_version, self.watchlist.db.conn.total_changes)     # total_changes: cualquier escritura en la base invalida
        hit = self._scan_memo.get(key)
        if hit is not None and now - hit[0] < SCAN_MEMO_SECONDS:
            return hit[1]
        value = build()
        self._scan_memo = {k: v for k, v in self._scan_memo.items() if now - v[0] < SCAN_MEMO_SECONDS}
        self._scan_memo[key] = (now, value)
        return value

    def _memo_result(self, name: str, criteria: ScanCriteria, extra, build):
        """Resultado de un cálculo del Scanner para los mismos criterios, datos y cartera: volver a un perfil ya visto no lo recalcula."""
        positions = self.state.positions
        try:
            key = (name, criteria, extra, id(positions), len(positions), self.nlv_usd(), self.now().date())
            hash(key)
        except TypeError:
            return build()
        return self._memo(repr(key), build)

    def _scan_snapshots(self):
        return self._memo("snapshots", self.snapshots.all)

    def _scan_closes(self, snapshots):
        return self._memo("closes", lambda: self.bars.all_closes({s.contract.ticker for s in snapshots}))

    def scan(self, criteria: ScanCriteria, include_rejections: bool = False) -> ScanOutput:
        return self._memo_result("scan", criteria, include_rejections, lambda: self._scan(criteria, include_rejections))

    def _scan(self, criteria: ScanCriteria, include_rejections: bool) -> ScanOutput:
        snapshots = self._scan_snapshots()
        bars = self._scan_closes(snapshots) if criteria.technical_active else None
        return run_scan(
            snapshots, self.infos(criteria), with_spreads(self.state.positions), criteria,
            self.now().date(), include_rejections, bars, self.settings.scanner.technical,
            self.quality_exempt(), self._atr(criteria, snapshots), self.nlv_usd(), self.spread_only_tickers(),
        )

    def scan_both(self, put_criteria: ScanCriteria, spread_criteria: ScanCriteria, include_rejections: bool = False) -> ScanOutput:
        """Modo «Ambos» con un juego de filtros para la put desnuda y otro para el spread: la put sale como put si pasa los de put y su strike
        cabe en `max_naked_strike` de `spread_criteria`; si no cabe, sale su spread si la vendida pasa los de spread."""
        put_out = self.scan(put_criteria.with_filters(spread_mode=False, mixed=False), include_rejections)
        spread_out = self.scan(spread_criteria.with_filters(spread_mode=True, mixed=False), include_rejections)
        mixed = build_mixed_sets(put_out.results, spread_out.results, spread_out.spreads, spread_criteria, with_spreads(self.state.positions),
                                 self.nlv_usd(), put_criteria.min_yield_pct, self.spread_only_tickers())
        return ScanOutput(put_out.results, put_out.rejected_count, {**spread_out.rejections, **put_out.rejections}, spread_out.spreads, mixed)

    def scan_profiles(self, sets: list[tuple[str, str, ScanCriteria]]) -> tuple[list, dict[str, dict[str, int]]]:
        """Un solo escaneo con los filtros de varios perfiles: `sets` = (perfil, instrumento `put`|`spread`, criterios). Cada contrato (o spread)
        que cumple los de algún perfil sale UNA vez, con la lista de perfiles que cumple. Devuelve (filas, {perfil: {instrumento: nº de filas}})."""
        put_hits: dict = {}
        spread_hits: dict = {}
        counts: dict[str, dict[str, int]] = {}
        for name, instrument, criteria in sets:
            if instrument == "spread":
                out = self.scan(criteria.with_filters(spread_mode=True, mixed=False))
                for sp in out.spreads:
                    c = sp.short.snapshot.contract
                    key = (c.ticker, c.expiry, c.strike, sp.long.contract.strike)
                    spread_hits.setdefault(key, (sp, []))[1].append(name)
                n = len(out.spreads)
            else:
                out = self.scan(criteria.with_filters(spread_mode=False, mixed=False))
                for r in out.results:
                    c = r.snapshot.contract
                    put_hits.setdefault((c.ticker, c.expiry, c.strike), (r, []))[1].append(name)
                n = len(out.results)
            counts.setdefault(name, {})[instrument] = n
        rows = build_profile_rows(put_hits, spread_hits, with_spreads(self.state.positions), self.nlv_usd())
        return rows, counts

    def split_tastytrade_profile(self) -> bool:
        """Migración única: el perfil guardado «Tastytrade» (put + spread) pasa a dos, «TastyPut» y «TastySpread», con los mismos filtros y color.
        Las puts del Panel que tenían asignado «Tastytrade» pasan a «TastyPut». True si cambió algo."""
        stored = self.stored_profiles()
        if stored is None:
            return False
        profiles, default = stored
        old = next((p for p in profiles if p.name == "Tastytrade" and len(p.instruments) > 1), None)
        if old is None or any(p.name in ("TastyPut", "TastySpread") for p in profiles):
            return False
        pair = [replace(old, name="TastyPut", instruments=("put",)), replace(old, name="TastySpread", instruments=("spread",))]
        out = [q for p in profiles for q in (pair if p is old else [p])]
        try:
            assigned = json.loads(self.meta.get(PUT_PROFILES) or "{}")      # en bruto: `put_profiles()` descarta los nombres que ya no existen
        except ValueError:
            assigned = {}
        self.save_profiles(out, "TastyPut" if default == "Tastytrade" else default)
        if any(v == "Tastytrade" for v in assigned.values()):
            self.meta.set(PUT_PROFILES, json.dumps({k: ("TastyPut" if v == "Tastytrade" else v) for k, v in assigned.items()}))
        log.info("Perfil «Tastytrade» dividido en «TastyPut» y «TastySpread»")
        return True

    def _atr(self, criteria: ScanCriteria, snapshots) -> Optional[dict[str, float]]:
        """ATR de los subyacentes, solo si el filtro está activo."""
        if criteria.min_atr_multiple is None:
            return None
        return self._memo("atr", lambda: self.bars.atr({s.contract.ticker for s in snapshots}, self.settings.scanner.technical.atr_period))

    def put_margins(self) -> dict[str, float]:
        """Margen what-if guardado (por contrato) de las puts vendidas de la cartera, por `put_key` (en un spread, de la pata vendida).
        Es el de abrir una put nueva con esos datos: sirve para saber cuánto margen libera cerrarla, no es el margen real que fija IBKR."""
        stored = {(norm_ticker(s.contract.ticker), s.contract.expiry, round(float(s.contract.strike), 4)): s.initial_margin
                  for s in self._scan_snapshots() if s.initial_margin is not None and s.contract.right is OptionRight.PUT}
        out: dict[str, float] = {}
        for p in self.state.positions:
            o = p.option
            if o is None or o.right is not OptionRight.PUT or p.quantity >= 0:
                continue
            m = stored.get((norm_ticker(o.ticker), o.expiry, round(float(o.strike), 4)))
            if m is not None:
                out[put_key_of(p)] = m
        return out

    def tech_summaries(self, out: ScanOutput) -> dict[tuple[str, float], TechSummary]:
        """Resumen técnico (informativo) de la pata vendida de cada fila del resultado: {(ticker, strike): TechSummary}."""
        shorts = list(out.results) + [m.short for m in out.mixed] + [sp.short for sp in out.spreads]
        wanted = {(r.snapshot.contract.ticker, r.snapshot.contract.strike): r.info.underlying_price if r.info else None for r in shorts}
        if not wanted:
            return {}
        bars = self._memo("closes_all", lambda: self.bars.all_closes(set(self.watchlist.list())))
        summarizer = TechSummarizer(bars, self.settings.scanner.technical, self.settings.scanner.strategy, self.now().date())
        found = ((key, summarizer.summary(key[0], price, key[1])) for key, price in wanted.items())
        return {key: s for key, s in found if s is not None}

    def owned_options(self) -> dict[tuple[str, date, float], float]:
        """Puts que ya tienes en cartera: (ticker normalizado, vencimiento, strike) -> contratos (negativo = vendidos, positivo = comprados)."""
        owned: dict[tuple[str, date, float], float] = {}
        for p in self.state.positions:
            o = p.option
            if o is None or o.right is not OptionRight.PUT or not p.quantity:
                continue
            key = (norm_ticker(o.ticker), o.expiry, round(float(o.strike), 4))
            owned[key] = owned.get(key, 0.0) + p.quantity
        return owned

    def nlv_usd(self) -> Optional[float]:
        """NLV de la cuenta en USD: con el tipo de cambio de IBKR y, si no llega, `scanner.spreads.usd_per_base`.
        None si no hay cuenta o tipo de cambio (los pesos sobre el NLV no se pueden calcular)."""
        acc = self.state.account
        if acc is None or acc.net_liquidation is None:
            return None
        if acc.nlv_usd is not None:
            return acc.nlv_usd
        if acc.base_currency == "USD":
            return acc.net_liquidation
        manual = self.settings.scanner.spreads.usd_per_base
        return acc.net_liquidation * manual if manual else None

    def spread_check(self, criteria: ScanCriteria) -> list[CheckRow]:
        """Para cada put vendida que pasa el escaneo COMO PUT SOLA (con su yield mínimo), qué condiciones del spread cumple y cuáles no."""
        put_scan = self.scan(criteria.with_filters(spread_mode=False, mixed=False))
        return spread_check(put_scan.results, self._scan_snapshots(), criteria, self.nlv_usd())

    def scan_impact(self, criteria: ScanCriteria) -> ImpactReport:
        """Cuánto descarta cada filtro activo (ver `scanner/impact.py`). En modo spread se mide la pata vendida, sin yield."""
        if criteria.spread_mode:
            criteria = criteria.with_filters(min_yield_pct=0.0)
        return self._memo_result("impact", criteria, None, lambda: self._scan_impact(criteria))

    def _scan_impact(self, criteria: ScanCriteria) -> ImpactReport:
        snapshots = self._scan_snapshots()
        bars = self._scan_closes(snapshots) if criteria.technical_active else None
        return impact_report(
            snapshots, self.infos(criteria), criteria, self.now().date(), bars, self.settings.scanner.technical,
            self.quality_exempt(), self._atr(criteria, snapshots),
        )

    def assignment(self) -> AssignmentExposure:
        return assignment_exposure(with_spreads(self.state.positions), self.state.account)

    def diversification(self) -> tuple[SectorExposure, list[WeekExposure]]:
        pos = with_spreads(self.state.positions)
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
            if sel.long_strike is not None:   # spread: el margen es su pérdida máxima (sin what-if)
                width = (sel.strike - sel.long_strike) * snap.contract.multiplier
                loss = sel.max_loss if sel.max_loss is not None and 0 < sel.max_loss <= width else width
                info = infos.get(sel.ticker)
                trades.append(SimulatedTrade(snap.contract, sel.quantity, info.sector if info else None, loss * sel.quantity, sel.long_strike))
                continue
            margin = snap.initial_margin * sel.quantity if snap.initial_margin is not None else None
            if margin is None and self.state.connected:
                try:
                    margin = await self.gateway.what_if_margin(snap.contract, sel.quantity)
                except BrokerError:
                    margin = None
            info = infos.get(sel.ticker)
            trades.append(SimulatedTrade(snap.contract, sel.quantity, info.sector if info else None, margin))
        return simulate(
            with_spreads(self.state.positions), trades, self.state.account, self.settings.risk.cushion_thresholds,
            self.now().date(), self.settings.diversification.weeks_ahead,
        )
