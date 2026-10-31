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
      ]
    }

`credit` es la prima NETA por acción (en un spread, vendida − comprada); `max_loss` la pérdida máxima de toda la posición en dólares
(opcional). Un elemento mal formado se descarta y se anota en `warnings`; un fichero que no es un JSON de este formato lanza
`CommunityError`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from scanner_opciones.feeds.base import ContractIdea

VERSION = 1
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
class CommunityData:
    generated_at: datetime
    tickers: tuple[CommunityTicker, ...] = ()
    ideas: tuple[CommunityIdea, ...] = ()
    warnings: tuple[str, ...] = field(default=(), compare=False)

    def by_ticker(self) -> dict[str, CommunityTicker]:
        return {t.ticker: t for t in self.tickers}


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


def parse_community(text: str) -> CommunityData:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise CommunityError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise CommunityError(f"se esperaba un objeto con version {VERSION}")
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
    return CommunityData(generated, tuple(tickers.values()), tuple(ideas), tuple(warnings))


def load_community(path: str | Path) -> CommunityData:
    p = Path(path)
    if not p.is_file():
        raise CommunityError(f"no existe {p}")
    try:
        return parse_community(p.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise CommunityError(f"no se pudo leer {p}: {exc}") from exc
