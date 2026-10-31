"""Libro de operaciones compartidas del Feed de la comunidad (`community_trades`). Una fila por operación (clave = su id del Feed), con su ciclo
apertura->cierre y las métricas de salida derivadas. Se acumula aunque la tarjeta salga de la ventana del Feed, para analizar el historial de
salida de cada trader. Solo acceso a datos; las métricas las calcula `community/feed_data.py`."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.community.feed_data import FeedLeg, FeedTrade
from scanner_opciones.storage.db import Database

# estrategias de crédito (venta de prima) para las que el % capturado y el DTE de salida son significativos
CREDIT_STRATEGIES = ("Short Put", "Bull Put Spread", "Short Call", "Bear Call Spread", "Iron Condor", "Short Strangle")

_COLS = ("id", "trader", "ticker", "strategy", "currency", "status", "opened", "closed", "pnl", "note", "legs", "close_legs",
         "credit_open", "close_cost", "pct_captured", "dte_open", "dte_close", "hold_days")


def _dump_legs(legs: Sequence[FeedLeg]) -> Optional[str]:
    if not legs:
        return None
    return json.dumps([{"action": leg.action, "right": leg.right, "qty": leg.qty, "price": leg.price,
                        "strike": leg.strike, "expiry": leg.expiry.isoformat() if leg.expiry else None,
                        "close_price": leg.close_price} for leg in legs])


def _values(t: FeedTrade) -> tuple:
    """Los valores de una operación en el orden de `_COLS`, con las patas en JSON y las métricas de salida ya calculadas."""
    close = [{"qty": leg.qty, "price": leg.close_price} for leg in t.legs if leg.close_price is not None]
    return (t.id, t.trader, t.ticker, t.strategy, t.currency, t.status, t.opened.isoformat(),
            t.closed.isoformat() if t.closed else None, t.pnl, t.note, _dump_legs(t.legs), json.dumps(close) if close else None,
            t.credit_open, t.close_cost, t.pct_captured, t.dte_open, t.dte_close, t.hold_days)


@dataclass(frozen=True)
class TradeRow:
    """Una operación guardada del Feed (fila de `community_trades`)."""
    id: str
    trader: str
    ticker: str
    strategy: str
    currency: Optional[str]
    status: str
    opened: str
    closed: Optional[str]
    pnl: Optional[float]
    note: Optional[str]
    credit_open: Optional[float]
    pct_captured: Optional[float]
    dte_open: Optional[int]
    dte_close: Optional[int]
    hold_days: Optional[int]
    first_seen: str
    last_seen: str


@dataclass(frozen=True)
class ExitStyle:
    """Resumen del estilo de salida de un trader (operaciones de crédito ya cerradas que hemos visto en el Feed)."""
    trader: str
    closed: int                         # operaciones de crédito cerradas
    wins: int                           # con pnl > 0
    win_rate: Optional[float]           # %
    median_hold_days: Optional[float]
    median_dte_close: Optional[float]   # DTE restante al cerrar (alto = sale pronto; ~0 = lleva a vencimiento)
    median_pct_captured: Optional[float]
    strategies: tuple[str, ...]         # estrategias de crédito que se le han visto cerrar, más frecuente primero

    @property
    def style_label(self) -> str:
        """Etiqueta corta del estilo de salida según el DTE mediano al cerrar: alto = sale pronto; cerca de 0 = lleva a vencimiento."""
        d = self.median_dte_close
        if d is None:
            return "Sin DTE de salida"
        if d <= 5:
            return "Lleva a vencimiento"
        if d <= 20:
            return f"Gestiona (~{d:g} DTE)"
        return "Cierra pronto"


def _row(r) -> TradeRow:
    return TradeRow(r["id"], r["trader"], r["ticker"], r["strategy"], r["currency"], r["status"], r["opened"], r["closed"],
                    r["pnl"], r["note"], r["credit_open"], r["pct_captured"], r["dte_open"], r["dte_close"], r["hold_days"],
                    r["first_seen"], r["last_seen"])


class CommunityTradeRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_many(self, trades: Sequence[FeedTrade], at: str) -> dict[str, int]:
        """Guarda las operaciones del Feed en una transacción: inserta las nuevas y actualiza las conocidas que cambian (sobre todo al pasar de
        abierta a cerrada). Nunca borra: una tarjeta que sale de la ventana del Feed se conserva. Devuelve {new, closed, updated}."""
        summary = {"new": 0, "closed": 0, "updated": 0}
        cols = ", ".join(_COLS)
        marks = ",".join("?" * len(_COLS))
        updatable = [c for c in _COLS if c != "id"]
        set_clause = ", ".join(f"{c} = ?" for c in updatable) + ", last_seen = ?, updated_at = ?"
        with self.db.conn:
            c = self.db.conn
            for t in trades:
                if not t.id:
                    continue
                old = c.execute("SELECT status FROM community_trades WHERE id = ?", (t.id,)).fetchone()
                vals = _values(t)
                if old is None:
                    c.execute(f"INSERT INTO community_trades ({cols}, first_seen, last_seen, updated_at) VALUES ({marks},?,?,?)", (*vals, at, at, at))
                    summary["new"] += 1
                    if t.status == "closed":
                        summary["closed"] += 1
                    continue
                c.execute(f"UPDATE community_trades SET {set_clause} WHERE id = ?", (*vals[1:], at, at, t.id))
                summary["updated"] += 1
                if old["status"] != "closed" and t.status == "closed":
                    summary["closed"] += 1
        return summary

    def all(self, trader: Optional[str] = None, status: Optional[str] = None, limit: int = 1000) -> list[TradeRow]:
        where, args = "", []
        if trader:
            where, args = where + " AND trader = ?", [*args, trader]
        if status:
            where, args = where + " AND status = ?", [*args, status]
        rows = self.db.conn.execute(
            f"SELECT * FROM community_trades WHERE 1 = 1{where} ORDER BY COALESCE(closed, opened) DESC, opened DESC LIMIT ?", (*args, limit)).fetchall()
        return [_row(r) for r in rows]

    def legs(self, trade_id: str) -> list[FeedLeg]:
        """Las patas guardadas de una operación (para el detalle); vacío si no están."""
        r = self.db.conn.execute("SELECT legs FROM community_trades WHERE id = ?", (trade_id,)).fetchone()
        if r is None or not r["legs"]:
            return []
        return [FeedLeg(d["action"], d["right"], d["qty"], d["price"], d.get("strike"),
                        date.fromisoformat(d["expiry"]) if d.get("expiry") else None, d.get("close_price")) for d in json.loads(r["legs"])]

    def traders(self) -> list[str]:
        return [r[0] for r in self.db.conn.execute("SELECT DISTINCT trader FROM community_trades ORDER BY trader COLLATE NOCASE").fetchall()]

    def counts(self) -> dict[str, int]:
        c = self.db.conn
        return {"trades": c.execute("SELECT COUNT(*) FROM community_trades").fetchone()[0],
                "closed": c.execute("SELECT COUNT(*) FROM community_trades WHERE status = 'closed'").fetchone()[0],
                "traders": c.execute("SELECT COUNT(DISTINCT trader) FROM community_trades").fetchone()[0]}

    def exit_styles(self, min_closed: int = 3) -> list[ExitStyle]:
        """Estilo de salida por trader a partir de sus operaciones de CRÉDITO ya cerradas (las únicas en las que el % capturado y el DTE de salida
        tienen sentido). Solo traders con al menos `min_closed`. Ordenado de más a menos operaciones cerradas. Las medianas se calculan aquí
        (SQLite no las trae) sobre lo que haya: un trader sin precios estructurados tendrá recuento y win rate pero no % capturado."""
        marks = ",".join("?" * len(CREDIT_STRATEGIES))
        rows = self.db.conn.execute(
            f"SELECT trader, strategy, pnl, pct_captured, dte_close, hold_days FROM community_trades "
            f"WHERE status = 'closed' AND strategy IN ({marks}) ORDER BY trader COLLATE NOCASE", CREDIT_STRATEGIES).fetchall()
        by_trader: dict[str, list] = {}
        for r in rows:
            by_trader.setdefault(r["trader"], []).append(r)
        out: list[ExitStyle] = []
        for trader, items in by_trader.items():
            if len(items) < min_closed:
                continue
            wins = sum(1 for r in items if r["pnl"] is not None and r["pnl"] > 0)
            graded = sum(1 for r in items if r["pnl"] is not None)
            strategies = sorted({r["strategy"] for r in items}, key=lambda s: -sum(1 for r in items if r["strategy"] == s))
            out.append(ExitStyle(
                trader=trader, closed=len(items), wins=wins,
                win_rate=wins / graded * 100 if graded else None,
                median_hold_days=_median(r["hold_days"] for r in items),
                median_dte_close=_median(r["dte_close"] for r in items),
                median_pct_captured=_median(r["pct_captured"] for r in items),
                strategies=tuple(strategies)))
        out.sort(key=lambda e: -e.closed)
        return out


def _median(values) -> Optional[float]:
    nums = [v for v in values if v is not None]
    return round(statistics.median(nums), 1) if nums else None
