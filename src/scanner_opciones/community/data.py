"""Lectura y validación del JSON de la comunidad (Tortuga Trades).

Formato (versión 1). Lo escribe la rutina diaria a partir del conector de Tortuga; la aplicación solo lo lee:

    {
      "version": 1,
      "generated_at": "2026-10-12T13:00:00+02:00",
      "source": "tortuga",
      "tickers": [   # del screener de la comunidad, estrategias «Short Put» y «Bull Put Spread»
        {"ticker": "PEP", "month_traders": 17, "month_operations": 45, "week_traders": 6, "week_operations": 10,
         "traders": ["JBJ", "yors37"]}
      ],
      "ideas": [     # aperturas del último día hábil con datos (llegan un día tarde: IBKR Flex)
        {"date": "2026-10-09", "trader": "Juancm", "ticker": "VZ", "strategy": "Short Put", "expiry": "2026-11-20",
         "short_strike": 42, "long_strike": null, "contracts": 1, "credit": 0.96, "max_loss": 4104}
      ],
      "profiles": {   # (versión 2, opcional) track record de cada trader que aparece en «ideas», de get_trader_profile
        "yors37": {"rank": 66, "tier": "gold", "score": 49.9, "twr": 25.13, "win_rate": 73.8,
                   "profit_factor": 1.24, "expectancy": 19.24, "total_pnl": 7271.9, "capital_return": 5.25, "trades": 378}
      }
    }

`credit` es la prima NETA por acción (en un spread, vendida − comprada); `max_loss` la pérdida máxima de toda la posición en dólares
(opcional). `profiles` es opcional (un JSON de versión 1 no lo lleva y sigue siendo válido): la fiabilidad del trader que la app muestra
en Comunidad y en el Panel. Un elemento mal formado se descarta y se anota en `warnings`; un fichero que no es un JSON de este formato
lanza `CommunityError`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from scanner_opciones.feeds.base import ContractIdea

VERSION = 2                     # se escribe la 2 (con `profiles`); se lee también la 1 (sin ellos)
SUPPORTED_VERSIONS = (1, 2)
STRATEGIES = ("Short Put", "Bull Put Spread")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-/][A-Z])?$")


class CommunityError(ValueError):
    """El fichero de la comunidad no existe o no tiene el formato esperado."""


@dataclass(frozen=True)
class CommunityTicker:
    ticker: str
    month_traders: int
    month_operations: int
    week_traders: int = 0
    week_operations: int = 0
    traders: tuple[str, ...] = ()           # los que comparten (los anónimos solo cuentan en `*_traders`)


CommunityIdea = ContractIdea      # una idea de la comunidad es un contrato de una fuente (`feeds/base.py`); el JSON no lleva `source`


@dataclass(frozen=True)
class TraderProfile:
    """Track record público de un trader (get_trader_profile): la fiabilidad que la app muestra junto a su nombre. Todo salvo
    `username` y `tier` puede ser None cuando la herramienta no lo trae."""
    username: str
    tier: str = ""                          # id del escalón del ranking: «gold», «silver», «bronze»…
    rank: Optional[int] = None
    score: Optional[float] = None
    twr: Optional[float] = None             # rentabilidad TWR del año, en %
    win_rate: Optional[float] = None        # % de operaciones ganadoras
    profit_factor: Optional[float] = None   # beneficio bruto / pérdida bruta (< 1 = pierde dinero)
    expectancy: Optional[float] = None      # resultado esperado por operación, en la divisa base del trader
    total_pnl: Optional[float] = None       # P&L total del periodo, en la divisa base del trader
    capital_return: Optional[float] = None  # retorno sobre el capital medio, en %
    trades: Optional[int] = None            # nº de operaciones del periodo


@dataclass(frozen=True)
class CommunityData:
    generated_at: datetime
    tickers: tuple[CommunityTicker, ...] = ()
    ideas: tuple[CommunityIdea, ...] = ()
    profiles: dict[str, TraderProfile] = field(default_factory=dict)   # por username en minúsculas
    warnings: tuple[str, ...] = field(default=(), compare=False)

    def by_ticker(self) -> dict[str, CommunityTicker]:
        return {t.ticker: t for t in self.tickers}

    def profile(self, trader: Optional[str]) -> Optional[TraderProfile]:
        """Perfil del trader (sin distinguir mayúsculas); None si no lo hay."""
        return self.profiles.get(trader.casefold()) if trader else None


def _num(raw: Any, label: str, minimum: float = 0.0, integer: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{label} no es un número")
    if raw < minimum:
        raise ValueError(f"{label} < {minimum:g}")
    if integer and int(raw) != raw:
        raise ValueError(f"{label} no es entero")
    return int(raw) if integer else float(raw)


def _ticker(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if not _TICKER.match(text):
        raise ValueError(f"ticker «{raw}» no válido")
    return text.replace("/", "-").replace(".", "-")      # como los escribe la aplicación («BRK-B»)


def _parse_ticker(item: Any) -> CommunityTicker:
    if not isinstance(item, dict):
        raise ValueError("no es un objeto")
    names = item.get("traders") or []
    if not isinstance(names, list):
        raise ValueError("traders debe ser una lista")
    return CommunityTicker(
        _ticker(item.get("ticker")),
        _num(item.get("month_traders"), "month_traders", integer=True), _num(item.get("month_operations"), "month_operations", integer=True),
        _num(item.get("week_traders", 0), "week_traders", integer=True), _num(item.get("week_operations", 0), "week_operations", integer=True),
        tuple(str(n) for n in names if n))


def _parse_idea(item: Any) -> CommunityIdea:
    if not isinstance(item, dict):
        raise ValueError("no es un objeto")
    strategy = item.get("strategy")
    if strategy not in STRATEGIES:
        raise ValueError(f"estrategia «{strategy}» no admitida")
    short = _num(item.get("short_strike"), "short_strike", minimum=0.0001)
    long_ = item.get("long_strike")
    long_strike = None if long_ is None else _num(long_, "long_strike", minimum=0.0001)
    if (strategy == "Bull Put Spread") != (long_strike is not None):
        raise ValueError("long_strike solo va en los Bull Put Spread")
    if long_strike is not None and long_strike >= short:
        raise ValueError("long_strike debe ser menor que short_strike")
    max_loss = item.get("max_loss")
    try:
        day, expiry = date.fromisoformat(str(item.get("date"))), date.fromisoformat(str(item.get("expiry")))
    except ValueError:
        raise ValueError("fecha no válida") from None
    if expiry < day:
        raise ValueError("vence antes de abrirse")
    trader = str(item.get("trader") or "").strip()
    if not trader:
        raise ValueError("falta el trader")
    return CommunityIdea(day, trader, _ticker(item.get("ticker")), strategy, expiry, short, long_strike,
                         _num(item.get("contracts", 1), "contracts", minimum=1, integer=True), _num(item.get("credit"), "credit"),
                         None if max_loss is None else _num(max_loss, "max_loss"))


def _opt_num(raw: Any, label: str, warnings: list[str], integer: bool = False) -> Optional[float]:
    """Número opcional de un perfil: None si falta o es inválido (se anota), sin tope mínimo (P&L y expectancy pueden ser negativos)."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        warnings.append(f"{label} no es un número")
        return None
    return int(raw) if integer else float(raw)


def _parse_profile(username: str, item: Any, warnings: list[str]) -> Optional[TraderProfile]:
    if not isinstance(item, dict):
        warnings.append(f"profiles[{username}]: no es un objeto")
        return None
    w = username.casefold()
    return TraderProfile(
        username=str(username), tier=str(item.get("tier") or "").strip().casefold(),
        rank=_opt_num(item.get("rank"), f"profiles[{w}].rank", warnings, integer=True),
        score=_opt_num(item.get("score"), f"profiles[{w}].score", warnings),
        twr=_opt_num(item.get("twr"), f"profiles[{w}].twr", warnings),
        win_rate=_opt_num(item.get("win_rate"), f"profiles[{w}].win_rate", warnings),
        profit_factor=_opt_num(item.get("profit_factor"), f"profiles[{w}].profit_factor", warnings),
        expectancy=_opt_num(item.get("expectancy"), f"profiles[{w}].expectancy", warnings),
        total_pnl=_opt_num(item.get("total_pnl"), f"profiles[{w}].total_pnl", warnings),
        capital_return=_opt_num(item.get("capital_return"), f"profiles[{w}].capital_return", warnings),
        trades=_opt_num(item.get("trades"), f"profiles[{w}].trades", warnings, integer=True))


def parse_community(text: str) -> CommunityData:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise CommunityError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") not in SUPPORTED_VERSIONS:
        raise CommunityError(f"se esperaba un objeto con version {' o '.join(map(str, SUPPORTED_VERSIONS))}")
    try:
        generated = datetime.fromisoformat(str(raw.get("generated_at")))
    except ValueError:
        raise CommunityError("generated_at no es una fecha ISO") from None
    if not isinstance(raw.get("tickers"), list):
        raise CommunityError("falta la lista «tickers»")
    warnings: list[str] = []
    tickers: dict[str, CommunityTicker] = {}
    for i, item in enumerate(raw["tickers"]):
        try:
            t = _parse_ticker(item)
        except ValueError as exc:
            warnings.append(f"tickers[{i}]: {exc}")
            continue
        tickers.setdefault(t.ticker, t)
    ideas: list[CommunityIdea] = []
    for i, item in enumerate(raw.get("ideas") or []):
        try:
            ideas.append(_parse_idea(item))
        except ValueError as exc:
            warnings.append(f"ideas[{i}]: {exc}")
    profiles: dict[str, TraderProfile] = {}
    raw_profiles = raw.get("profiles")
    if raw_profiles is not None and not isinstance(raw_profiles, dict):
        warnings.append("«profiles» no es un objeto")
    elif raw_profiles:
        for username, item in raw_profiles.items():
            prof = _parse_profile(str(username), item, warnings)
            if prof is not None:
                profiles[str(username).casefold()] = prof
    return CommunityData(generated, tuple(tickers.values()), tuple(ideas), profiles, tuple(warnings))


def load_community(path: str | Path) -> CommunityData:
    p = Path(path)
    if not p.is_file():
        raise CommunityError(f"no existe {p}")
    try:
        return parse_community(p.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise CommunityError(f"no se pudo leer {p}: {exc}") from exc
