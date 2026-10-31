"""Lectura y validación del JSON del Feed de la comunidad (Tortuga Trades, herramienta `list_community_trades`).

A diferencia de las «ideas» (`community/data.py`, solo Short Put y Bull Put Spread abiertas, que se valoran y comparan con los perfiles),
el Feed trae las operaciones que los traders COMPARTEN tal cual: cualquier estrategia, abiertas y CERRADAS (con su P&L realizado) y la
tesis que escribe el trader (`note`). No se valora ni se compara: es informativo. Lo escribe la rutina diaria; la aplicación solo lo lee.

Formato (versión 1):

    {
      "version": 1,
      "generated_at": "2026-10-31T13:00:00+02:00",
      "source": "tortuga",
      "trades": [
        {"id": "cb8a…", "trader": "A.A.G", "ticker": "MSFT", "strategy": "Iron Condor", "status": "open",
         "opened": "2026-10-30", "closed": null, "pnl": null, "currency": "USD",
         "legs": "SELL 1 550C / BUY 1 555C / BUY 1 485P / SELL 1 490P · vence 06/11", "note": null},
        {"id": "7e30…", "trader": "Kike", "ticker": "AMZN", "strategy": "Long Call", "status": "closed",
         "opened": "2026-10-30", "closed": "2026-10-30", "pnl": 83.42, "currency": "USD",
         "legs": "BUY 3 245C · vence 02/11", "note": "entro por el hueco"}
      ]
    }

`pnl` es el resultado realizado en la moneda de la operación (`currency`), solo en las cerradas (null en las abiertas). Un elemento mal
formado se descarta y se anota en `warnings`; un fichero que no es un JSON de este formato lanza `FeedError`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

VERSION = 1
STATUSES = ("open", "closed")
_TICKER_MAX = 12            # el feed trae índices y FX raros (SPXW, EUR.USD): se aceptan tal cual, solo se acota la longitud


class FeedError(ValueError):
    """El fichero del Feed de la comunidad no existe o no tiene el formato esperado."""


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
    legs: str = ""                          # descripción compacta de las patas (la arma la rutina)
    note: Optional[str] = None              # tesis que escribió el trader al compartir, si la hay


@dataclass(frozen=True)
class FeedData:
    generated_at: datetime
    trades: tuple[FeedTrade, ...] = ()
    warnings: tuple[str, ...] = field(default=(), compare=False)


def _opt_date(raw: Any) -> Optional[date]:
    if raw in (None, ""):
        return None
    return date.fromisoformat(str(raw))


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
    return FeedTrade(str(item.get("id") or ""), trader, ticker.replace("/", "-").replace(".", "-"), str(item.get("strategy") or "").strip() or "—",
                     status, opened, closed, None if pnl is None else float(pnl), str(item.get("currency") or "").strip().upper(),
                     str(item.get("legs") or "").strip(), note)


def parse_feed(text: str) -> FeedData:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise FeedError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise FeedError(f"se esperaba un objeto con version {VERSION}")
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
