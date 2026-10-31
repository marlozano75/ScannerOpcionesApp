"""Lectura y validación del JSON del Feed de la comunidad (Tortuga Trades, herramienta `list_community_trades`).

A diferencia de las «ideas» (`community/data.py`, solo Short Put y Bull Put Spread abiertas, que se valoran y comparan con los perfiles),
el Feed trae las operaciones que los traders COMPARTEN tal cual: cualquier estrategia, abiertas y CERRADAS (con su P&L realizado) y la
tesis que escribe el trader (`note`). No se valora ni se compara: es informativo. Lo escribe la rutina diaria; la aplicación solo lo lee.

Formato: se lee la versión 1 (patas en un texto ya armado) y la versión 2 (patas estructuradas, para analizar la salida):

    {
      "version": 2,
      "generated_at": "2026-10-31T13:00:00+02:00",
      "source": "tortuga",
      "trades": [
        {"id": "7e30…", "trader": "Kike", "ticker": "AMZN", "strategy": "Long Call", "status": "closed",
         "opened": "2026-10-30", "closed": "2026-10-30", "pnl": 83.42, "currency": "USD", "note": "entro por el hueco",
         "legs": [{"action": "BUY", "right": "C", "strike": 245, "expiry": "2026-11-02", "qty": 3, "price": 0.8}],
         "close_legs": [{"qty": 3, "price": 1.09}]}
      ]
    }

En la versión 1 `legs` es un texto («SELL 1 490P / BUY 1 485P · vence 06/11») y no hay `close_legs`: la operación se guarda igual, pero sin
las métricas de salida derivadas (no se puede calcular el crédito ni el % de prima capturado). En la versión 2 `legs` es una lista de patas
con su precio de apertura y `close_legs` (opcional) los precios de cierre, EN EL MISMO ORDEN que `legs`. `pnl` es el resultado realizado en la
moneda de la operación (`currency`), solo en las cerradas. Un elemento mal formado se descarta y se anota en `warnings`; un fichero que no es
un JSON de este formato lanza `FeedError`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

VERSION = 2                     # se escribe la 2 (patas estructuradas); se lee también la 1 (patas en texto)
SUPPORTED_VERSIONS = (1, 2)
STATUSES = ("open", "closed")
MULTIPLIER = 100                # las opciones compartidas son de acciones/índices de EE. UU. (100 por contrato)
_TICKER_MAX = 12            # el feed trae índices y FX raros (SPXW, EUR.USD): se aceptan tal cual, solo se acota la longitud
_RIGHTS = ("C", "P", "STK")


class FeedError(ValueError):
    """El fichero del Feed de la comunidad no existe o no tiene el formato esperado."""


@dataclass(frozen=True)
class FeedLeg:
    """Una pata de la operación, con su precio de apertura. `strike`/`expiry` solo en opciones (`right` C o P); `right` STK = acción."""
    action: str                             # «BUY» | «SELL»
    right: str                              # «C» | «P» | «STK»
    qty: float
    price: float
    strike: Optional[float] = None
    expiry: Optional[date] = None
    close_price: Optional[float] = None     # precio al que se cerró esta pata (de `close_legs`, por orden), si se cerró

    @property
    def is_option(self) -> bool:
        return self.right in ("C", "P")

    @property
    def sign(self) -> int:
        """+1 si se vendió (cobra prima), −1 si se compró (paga)."""
        return 1 if self.action == "SELL" else -1


@dataclass(frozen=True)
class FeedTrade:
    id: str
    trader: str
    ticker: str
    strategy: str
    status: str                             # «open» | «closed»
    opened: date
    closed: Optional[date] = None
    pnl: Optional[float] = None             # resultado realizado (solo cerradas), en `currency`
    currency: str = ""
    note: Optional[str] = None              # tesis que escribió el trader al compartir, si la hay
    legs: tuple[FeedLeg, ...] = ()          # patas estructuradas (vacío si el JSON es de la versión 1)
    legs_text: str = ""                     # descripción compacta de las patas (la trae la v1; en la v2 se arma a partir de `legs`)

    @property
    def has_stock(self) -> bool:
        return any(not leg.is_option for leg in self.legs)

    @property
    def credit_open(self) -> Optional[float]:
        """Prima NETA cobrada al abrir toda la posición ($): Σ(signo × precio × cantidad) × 100 de las patas de opción. Positiva = crédito.
        None si no hay patas de opción o si hay acciones mezcladas (el P&L no sería comparable con la prima)."""
        options = [leg for leg in self.legs if leg.is_option]
        if not options or self.has_stock:
            return None
        return sum(leg.sign * leg.price * leg.qty for leg in options) * MULTIPLIER

    @property
    def close_cost(self) -> Optional[float]:
        """Importe NETO al cerrar la posición ($), con el mismo signo que la apertura: Σ(signo × precio de cierre × cantidad) × 100. None si falta
        algún precio de cierre o hay acciones. El % capturado se basa en el `pnl` realizado, no en esto (lleva comisiones); es informativo."""
        options = [leg for leg in self.legs if leg.is_option]
        if not options or self.has_stock or any(leg.close_price is None for leg in options):
            return None
        return sum(leg.sign * (leg.close_price or 0.0) * leg.qty for leg in options) * MULTIPLIER

    @property
    def pct_captured(self) -> Optional[float]:
        """% de la prima de apertura que quedó como beneficio realizado (solo operaciones de crédito cerradas): `pnl` / crédito de apertura × 100.
        Se basa en el `pnl` (neto de comisiones) para no depender del emparejamiento de patas. None si no aplica."""
        credit = self.credit_open
        if self.status != "closed" or self.pnl is None or credit is None or credit <= 0:
            return None
        return self.pnl / credit * 100

    @property
    def short_expiry(self) -> Optional[date]:
        """El vencimiento más cercano de las patas de opción (el que gobierna la posición); None si no hay opciones."""
        expiries = [leg.expiry for leg in self.legs if leg.is_option and leg.expiry is not None]
        return min(expiries) if expiries else None

    @property
    def dte_open(self) -> Optional[int]:
        exp = self.short_expiry
        return (exp - self.opened).days if exp is not None else None

    @property
    def dte_close(self) -> Optional[int]:
        exp = self.short_expiry
        return (exp - self.closed).days if exp is not None and self.closed is not None else None

    @property
    def hold_days(self) -> Optional[int]:
        return (self.closed - self.opened).days if self.closed is not None else None


@dataclass(frozen=True)
class FeedData:
    generated_at: datetime
    trades: tuple[FeedTrade, ...] = ()
    warnings: tuple[str, ...] = field(default=(), compare=False)


def _opt_date(raw: Any) -> Optional[date]:
    if raw in (None, ""):
        return None
    return date.fromisoformat(str(raw))


def _num(raw: Any, label: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{label} no es un número")
    return float(raw)


def _leg_text(leg: FeedLeg) -> str:
    if not leg.is_option:
        return f"{leg.action} {leg.qty:g} acc"
    return f"{leg.action} {leg.qty:g} {leg.strike:g}{leg.right}"


def _parse_legs(raw: Any, close_raw: Any) -> tuple[tuple[FeedLeg, ...], str]:
    """Patas estructuradas de la versión 2 y su texto para mostrar. `close_raw` (opcional) trae los precios de cierre, por orden."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("legs debe ser una lista no vacía")
    closes = close_raw if isinstance(close_raw, list) else []
    legs: list[FeedLeg] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("una pata no es un objeto")
        action = str(item.get("action") or "").strip().upper()
        if action not in ("BUY", "SELL"):
            raise ValueError(f"action «{action}» no válida")
        right = str(item.get("right") or "").strip().upper()
        if right not in _RIGHTS:
            raise ValueError(f"right «{right}» no válido")
        qty, price = _num(item.get("qty"), "qty"), _num(item.get("price", 0), "price")
        strike = None if item.get("strike") in (None, "") else _num(item.get("strike"), "strike")
        expiry = _opt_date(item.get("expiry"))
        if right in ("C", "P") and strike is None:
            raise ValueError("una opción necesita strike")
        close_price = None
        if i < len(closes) and isinstance(closes[i], dict) and closes[i].get("price") is not None:
            close_price = _num(closes[i].get("price"), "close price")
        legs.append(FeedLeg(action, right, qty, price, strike, expiry, close_price))
    expiries = {leg.expiry for leg in legs if leg.is_option and leg.expiry is not None}
    text = " / ".join(_leg_text(leg) for leg in legs)
    if len(expiries) == 1:
        text += " · vence " + next(iter(expiries)).strftime("%d/%m")
    return tuple(legs), text


def _parse_trade(item: Any) -> FeedTrade:
    if not isinstance(item, dict):
        raise ValueError("no es un objeto")
    status = item.get("status")
    if status not in STATUSES:
        raise ValueError(f"status «{status}» no admitido")
    ticker = str(item.get("ticker") or "").strip().upper()
    if not ticker or len(ticker) > _TICKER_MAX:
        raise ValueError(f"ticker «{item.get('ticker')}» no válido")
    trader = str(item.get("trader") or "").strip()
    if not trader:
        raise ValueError("falta el trader")
    try:
        opened = date.fromisoformat(str(item.get("opened")))
        closed = _opt_date(item.get("closed"))
    except ValueError:
        raise ValueError("fecha no válida") from None
    if closed is not None and closed < opened:
        raise ValueError("se cerró antes de abrirse")
    pnl = item.get("pnl")
    if pnl is not None and (isinstance(pnl, bool) or not isinstance(pnl, (int, float))):
        raise ValueError("pnl no es un número")
    note = item.get("note")
    note = str(note).strip() if note not in (None, "") else None
    raw_legs = item.get("legs")
    if isinstance(raw_legs, list):                       # versión 2: patas estructuradas
        legs, legs_text = _parse_legs(raw_legs, item.get("close_legs"))
    else:                                                # versión 1: las patas ya venían en un texto
        legs, legs_text = (), str(raw_legs or "").strip()
    return FeedTrade(str(item.get("id") or ""), trader, ticker.replace("/", "-").replace(".", "-"), str(item.get("strategy") or "").strip() or "—",
                     status, opened, closed, None if pnl is None else float(pnl), str(item.get("currency") or "").strip().upper(), note, legs, legs_text)


def parse_feed(text: str) -> FeedData:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise FeedError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") not in SUPPORTED_VERSIONS:
        raise FeedError(f"se esperaba un objeto con version {' o '.join(map(str, SUPPORTED_VERSIONS))}")
    try:
        generated = datetime.fromisoformat(str(raw.get("generated_at")))
    except ValueError:
        raise FeedError("generated_at no es una fecha ISO") from None
    if not isinstance(raw.get("trades"), list):
        raise FeedError("falta la lista «trades»")
    trades: list[FeedTrade] = []
    warnings: list[str] = []
    for i, item in enumerate(raw["trades"]):
        try:
            trades.append(_parse_trade(item))
        except ValueError as exc:
            warnings.append(f"trades[{i}]: {exc}")
    return FeedData(generated, tuple(trades), tuple(warnings))


def load_feed(path: str | Path) -> FeedData:
    p = Path(path)
    if not p.is_file():
        raise FeedError(f"no existe {p}")
    try:
        return parse_feed(p.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise FeedError(f"no se pudo leer {p}: {exc}") from exc
