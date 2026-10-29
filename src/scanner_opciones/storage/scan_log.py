"""Registro del escaneo automático de perfiles: cuándo aparece cada contrato (o spread) en un perfil y cuándo deja de aparecer. Solo acceso a datos."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.storage.db import Database


@dataclass(frozen=True)
class Hit:
    """Un contrato (put) o spread que cumple los filtros de un perfil en una pasada."""
    ticker: str
    expiry: str                       # ISO
    strike: float                     # put vendida
    long_strike: Optional[float] = None    # put comprada (solo spreads)
    price: Optional[float] = None     # precio de referencia de la put, o crédito del spread
    yield_ann: Optional[float] = None  # yield anualizado de la put, o retorno sobre riesgo anualizado del spread (%)
    dte: Optional[int] = None
    distance_pct: Optional[float] = None   # descuento del strike sobre el precio del subyacente (%)

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0)


@dataclass(frozen=True)
class LoggedRow:
    """Un episodio de un perfil (fila de `scan_log`)."""
    id: int
    profile: str
    kind: str
    ticker: str
    expiry: str
    strike: float
    long_strike: Optional[float]
    first_seen: str
    last_seen: str
    ended_at: Optional[str]
    end_reason: Optional[str]
    active: bool
    first_price: Optional[float]
    first_yield: Optional[float]
    price: Optional[float]
    yield_ann: Optional[float]
    dte: Optional[int]
    distance_pct: Optional[float]

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0)

    @property
    def rejection_key(self) -> str:
        """Clave con la que `ScanOutput.rejections` describe la pata vendida."""
        return f"{self.ticker} {self.expiry} {self.strike}"


def _row(r) -> LoggedRow:
    return LoggedRow(r["id"], r["profile"], r["kind"], r["ticker"], r["expiry"], r["strike"], r["long_strike"], r["first_seen"], r["last_seen"],
                     r["ended_at"], r["end_reason"], bool(r["active"]), r["first_price"], r["first_yield"], r["price"], r["yield_ann"], r["dte"],
                     r["distance_pct"])


class ScanLogRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def active(self, profile: str, kind: str) -> list[LoggedRow]:
        rows = self.db.conn.execute("SELECT * FROM scan_log WHERE profile = ? AND kind = ? AND active = 1", (profile, kind)).fetchall()
        return [_row(r) for r in rows]

    def all_active(self) -> list[LoggedRow]:
        rows = self.db.conn.execute("SELECT * FROM scan_log WHERE active = 1 ORDER BY profile, kind, ticker, expiry, strike").fetchall()
        return [_row(r) for r in rows]

    def apply(self, at: str, profile: str, kind: str, appeared: Sequence[Hit], kept: Sequence[tuple[LoggedRow, Hit]],
              vanished: Sequence[tuple[LoggedRow, str]], skipped_tickers: int) -> None:
        """Una pasada en una sola transacción: abre los episodios nuevos, actualiza los que siguen, cierra los que se fueron (con su motivo) y
        anota la pasada."""
        with self.db.conn:
            c = self.db.conn
            for h in appeared:
                c.execute("INSERT INTO scan_log (profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, first_price, first_yield,"
                          " price, yield_ann, dte, distance_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, at, at, h.price, h.yield_ann, h.price, h.yield_ann, h.dte,
                           h.distance_pct))
                self._event(at, "aparece", profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, None, h.price, h.yield_ann, h.dte,
                            h.distance_pct)
            for row, h in kept:
                c.execute("UPDATE scan_log SET last_seen = ?, price = ?, yield_ann = ?, dte = ?, distance_pct = ? WHERE id = ?",
                          (at, h.price, h.yield_ann, h.dte, h.distance_pct, row.id))
            for row, reason in vanished:
                c.execute("UPDATE scan_log SET active = 0, ended_at = ?, end_reason = ? WHERE id = ?", (at, reason, row.id))
                self._event(at, "desaparece", profile, kind, row.ticker, row.expiry, row.strike, row.long_strike, reason, row.price, row.yield_ann,
                            row.dte, row.distance_pct)
            c.execute("INSERT INTO scan_runs (at, profile, kind, found, appeared, vanished, skipped_tickers) VALUES (?,?,?,?,?,?,?)",
                      (at, profile, kind, len(appeared) + len(kept), len(appeared), len(vanished), skipped_tickers))

    def _event(self, at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct) -> None:
        self.db.conn.execute("INSERT INTO scan_events (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte,"
                             " distance_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct))

    # ---- lo que aún no está en el Google Sheet ------------------------------------------------
    def unsynced_events(self, limit: int = 5000) -> list:
        return self.db.conn.execute("SELECT * FROM scan_events WHERE synced = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()

    def unsynced_runs(self, limit: int = 5000) -> list:
        return self.db.conn.execute("SELECT * FROM scan_runs WHERE synced = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mark_synced(self, events: Sequence[int] = (), runs: Sequence[int] = ()) -> None:
        with self.db.conn:
            self.db.conn.executemany("UPDATE scan_events SET synced = 1 WHERE id = ?", [(i,) for i in events])
            self.db.conn.executemany("UPDATE scan_runs SET synced = 1 WHERE id = ?", [(i,) for i in runs])
