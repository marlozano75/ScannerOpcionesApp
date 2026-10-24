"""Rutinas diarias de lectura de Tortuga Trades que lanza la propia aplicación (antes eran tareas programadas de `scripts/`).

Cada rutina pide a la CLI de Claude (solo herramientas de lectura del conector) un JSON, lo valida con el mismo código que usa después la
aplicación y SOLO entonces sustituye el fichero bueno (el anterior se conserva si algo falla). El estado de cada una (última vez que salió
bien, último intento, error) se guarda en `meta` para que sobreviva a un reinicio. Una rutina toca a su hora local una vez al día, solo en días hábiles (fin de semana y festivo esperan al siguiente, a su hora), y,
tras un fallo, no se reintenta hasta pasados `tortuga.retry_minutes`. Las de `tortuga.intervals` (radar) se repiten solo con el mercado
abierto y hacen una pasada más cuando se abre la app con el mercado cerrado si les falta la foto del último cierre."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from scanner_opciones.community.data import CommunityError, parse_community
from scanner_opciones.config.settings import Settings
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.portfolio.openings import OpeningsError, parse_openings
from scanner_opciones.storage.repositories import MetaRepo
from scanner_opciones.tortuga.api import API_ROUTINES, TortugaApi, TortugaApiError
from scanner_opciones.tortuga.claude_runner import ClaudeRunError, ToolCall, extract_json, run_claude, run_claude_calls, tool_names
from scanner_opciones.tortuga.investors import InvestorsError, build_investors_document, parse_investors
from scanner_opciones.tortuga.radar import RadarError, build_radar_document, parse_radar
from scanner_opciones.tortuga.schedule import load_schedule

log = logging.getLogger(__name__)
STATE_KEY = "tortuga_routines"
ClaudeRun = Callable[..., Awaitable[str]]
ClaudeCalls = Callable[..., Awaitable[list]]


class RoutineError(RuntimeError):
    """La respuesta de Claude no es válida; se conserva el fichero anterior."""


@dataclass(frozen=True)
class Routine:
    key: str
    title: str
    prompt: str                         # fichero dentro de `tortuga.prompts_dir`
    tools: tuple[str, ...]
    max_turns: int
    produces: str                       # qué deja y quién lo usa (para la pantalla)
    via_calls: bool = False             # True: se usa el resultado bruto de las herramientas, no el texto del modelo


ROUTINES = (
    Routine("aperturas", "Tus aperturas y cierres de puts", "aperturas_prompt.md", ("get_context", "list_trades"), 25,
            "fechas de apertura (perfil de cada put en el Panel) y cierres recientes (insignia en Ideas)"),
    Routine("comunidad", "Comunidad de traders", "comunidad_prompt.md", ("get_context", "get_community_screener", "list_public_operations"), 25,
            "columna Comunidad del Scanner, pestaña Ideas y fuente TortugaTrades_Dia"),
    Routine("radar_spreads", "Radar de Bull Put Spreads", "radar_spreads_prompt.md", ("get_context", "get_market_radar"), 30,
            "pestaña Tortuga y fuente TortugaSpreads (pasa a la watchlist)", via_calls=True),
    Routine("super_investors", "Super Investors (13F de gestores de calidad)", "super_investors_prompt.md", ("get_super_investors",), 30,
            "fuente SuperInvestors del Universo (empresas de calidad para puts)", via_calls=True),
)
BY_KEY = {r.key: r for r in ROUTINES}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def finish(settings: Settings, key: str, text: str) -> str:
    """Valida el JSON de `key` y, solo si es válido, sustituye el fichero. Devuelve un resumen. Lanza RoutineError si no vale."""
    try:
        if key == "comunidad":
            data = parse_community(text)
            if not data.tickers:
                raise RoutineError("no hay ningún ticker")
            _atomic_write(Path(settings.community.path), text)
            return f"{len(data.tickers)} tickers, {len(data.ideas)} ideas"
        if key == "aperturas":
            data = parse_openings(json.loads(text))
            _atomic_write(Path(settings.community.openings_path), text)
            return f"{len(data.opened)} contratos abiertos, {len(data.closes)} cierres"
    except (CommunityError, OpeningsError, RadarError, ValueError) as exc:
        raise RoutineError(str(exc)) from exc
    raise RoutineError(f"rutina desconocida: {key}")


def finish_calls(settings: Settings, key: str, calls: list[ToolCall], now: datetime) -> str:
    """Como `finish`, para las rutinas que usan el resultado bruto de las herramientas (radar de Bull Put Spreads)."""
    if key == "super_investors":
        try:
            doc = build_investors_document(calls, now, settings.super_investors.managers)
            data = parse_investors(doc)
        except (InvestorsError, ValueError) as exc:
            raise RoutineError(str(exc)) from exc
        _atomic_write(Path(settings.super_investors.path), json.dumps(doc, ensure_ascii=False))
        return f"{len(data.managers)} gestores, {sum(len(m.holdings) for m in data.managers)} posiciones"
    if key != "radar_spreads":
        raise RoutineError(f"rutina desconocida: {key}")
    try:
        doc = build_radar_document(calls, now)
        data = parse_radar(doc)            # lo que se escribe tiene que ser válido
    except (RadarError, ValueError) as exc:
        raise RoutineError(str(exc)) from exc
    _atomic_write(Path(settings.tortuga.radar_path), json.dumps(doc, ensure_ascii=False))
    spreads = sum(1 for r in data.rows if r.kind == "spread")
    return f"{len(data.rows)} valores ({spreads} Bull Put Spread, {len(data.rows) - spreads} venta de put)"


class RoutineRunner:
    def __init__(self, settings: Settings, meta: MetaRepo, now: Callable[[], datetime] = datetime.now, run: ClaudeRun = run_claude,
                 base_dir: Optional[Path] = None, run_calls: ClaudeCalls = run_claude_calls, api: Optional[TortugaApi] = None) -> None:
        self.settings, self.meta, self.now, self._run, self._run_calls, self._api = settings, meta, now, run, run_calls, api
        self.base_dir = base_dir or Path.cwd()
        self.market = MarketCalendar.from_settings(settings.market)
        self.running: Optional[str] = None

    # ---- estado ------------------------------------------------------------------------------
    def state(self) -> dict[str, dict]:
        try:
            return json.loads(self.meta.get(STATE_KEY) or "{}")
        except ValueError:
            return {}

    def _save(self, key: str, **fields) -> None:
        state = self.state()
        state.setdefault(key, {}).update(fields)
        self.meta.set(STATE_KEY, json.dumps(state))

    def due(self) -> list[Routine]:
        """Rutinas que tocan ya según su horario (`tortuga/schedule.py`: el de la configuración o el editado en Admisión): en mercado y fuera de él
        cada N minutos, a horas fijas, y una pasada tras el cierre. Tras un fallo se espera `tortuga.retry_minutes`."""
        cfg, now, state = self.settings.tortuga, self.now(), self.state()
        out = []
        for r in ROUTINES:
            st = state.get(r.key, {})
            if load_schedule(self.settings, self.meta, r.key).is_due(now, self.market, _parse(st.get("last_try")), _parse(st.get("last_ok")),
                                                                     cfg.retry_minutes, cfg.min_days.get(r.key)):
                out.append(r)
        return out

    # ---- ejecución ---------------------------------------------------------------------------
    async def run_one(self, key: str) -> tuple[bool, str]:
        """Ejecuta una rutina. Devuelve (salió bien, resumen o motivo del fallo). Nunca lanza: el fallo queda en el estado."""
        r = BY_KEY[key]
        cfg = self.settings.tortuga
        self.running = key
        self._save(key, last_try=self.now().isoformat(timespec="seconds"))
        try:
            if cfg.api_token and key in API_ROUTINES:            # API directa: sin CLI de Claude (la comunidad no está en la API)
                summary = await self._run_api(key)
                self._save(key, last_ok=self.now().isoformat(timespec="seconds"), error=None, summary=summary)
                log.info("Tortuga · %s (API): %s", r.title, summary)
                return True, summary
            prompt = (self.base_dir / cfg.prompts_dir / r.prompt).read_text(encoding="utf-8")
            prompt = prompt.replace("{managers}", "\n".join(f"- {m}" for m in self.settings.super_investors.managers))
            if r.via_calls:
                calls = await self._run_calls(cfg.command, prompt, tool_names(*r.tools), cfg.model, r.max_turns, cfg.timeout_seconds)
                summary = finish_calls(self.settings, key, calls, self.now())
            else:
                text = await self._run(cfg.command, prompt, tool_names(*r.tools), cfg.model, r.max_turns, cfg.timeout_seconds)
                summary = finish(self.settings, key, extract_json(text))
        except (ClaudeRunError, RoutineError, TortugaApiError, OSError) as exc:
            log.warning("Tortuga · %s: %s (se conserva lo anterior)", r.title, exc)
            self._save(key, error=str(exc))
            return False, str(exc)
        finally:
            self.running = None
        log.info("Tortuga · %s: %s", r.title, summary)
        self._save(key, last_ok=self.now().isoformat(timespec="seconds"), error=None, summary=summary)
        return True, summary

    async def _run_api(self, key: str) -> str:
        api = self._api or TortugaApi(self.settings.tortuga.api_token)
        if key == "aperturas":
            return finish(self.settings, key, await api.openings_document(self.now()))
        if key == "radar_spreads":
            return finish_calls(self.settings, key, await api.radar_calls(), self.now())
        return finish_calls(self.settings, key, await api.investor_calls(self.settings.super_investors.managers), self.now())


def _parse(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
