"""Registro del escaneo automático de perfiles: cuándo aparece cada contrato (o spread) en un perfil y cuándo deja de aparecer. Solo acceso a datos."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
    credit: Optional[float] = None         # prima cobrada por contrato ($, neta de comisiones)
    risk: Optional[float] = None           # capital en riesgo por contrato ($): (strike − prima) × multiplicador, o pérdida máxima del spread
    delta: Optional[float] = None          # delta de la put vendida
    long_delta: Optional[float] = None     # delta de la put comprada (solo spreads)
    yield_bid: Optional[float] = None      # yield anualizado vendiendo al bid (%; solo puts)
    iv_rank: Optional[float] = None
    iv_pct: Optional[float] = None         # IV Percentile
    spread_pct: Optional[float] = None     # spread bid/ask de la put vendida (% del mid)
    bid_size: Optional[int] = None

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0)

    @property
    def extras(self) -> tuple:
        return tuple(getattr(self, f) for f in EXTRA_FIELDS)


# columnas de `scan_log` y `scan_events` que se añadieron al registro (migración v31), en el orden en que se guardan y se muestran
EXTRA_FIELDS = ("credit", "risk", "delta", "long_delta", "yield_bid", "iv_rank", "iv_pct", "spread_pct", "bid_size")
_EXTRA_COLS = ", ".join(EXTRA_FIELDS)
_EXTRA_MARKS = ",".join("?" * len(EXTRA_FIELDS))
_EXTRA_SET = ", ".join(f"{f} = ?" for f in EXTRA_FIELDS)


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
    credit: Optional[float] = None
    risk: Optional[float] = None
    delta: Optional[float] = None
    long_delta: Optional[float] = None
    yield_bid: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_pct: Optional[float] = None
    spread_pct: Optional[float] = None
    bid_size: Optional[int] = None

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0)

    @property
    def extras(self) -> tuple:
        return tuple(getattr(self, f) for f in EXTRA_FIELDS)

    @property
    def rejection_key(self) -> str:
        """Clave con la que `ScanOutput.rejections` describe la pata vendida."""
        return f"{self.ticker} {self.expiry} {self.strike}"


def _row(r) -> LoggedRow:
    return LoggedRow(r["id"], r["profile"], r["kind"], r["ticker"], r["expiry"], r["strike"], r["long_strike"], r["first_seen"], r["last_seen"],
                     r["ended_at"], r["end_reason"], bool(r["active"]), r["first_price"], r["first_yield"], r["price"], r["yield_ann"], r["dte"],
                     r["distance_pct"], *(r[f] for f in EXTRA_FIELDS))


class ScanLogRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def active(self, profile: str, kind: str) -> list[LoggedRow]:
        rows = self.db.conn.execute("SELECT * FROM scan_log WHERE profile = ? AND kind = ? AND active = 1", (profile, kind)).fetchall()
        return [_row(r) for r in rows]

    def profiles_showing(self, kind: str, ticker: str, expiry: date, strike: float, long_strike: Optional[float], day: date) -> list[str]:
        """Perfiles cuyo escaneo mostraba ese contrato (o spread, con `long_strike`) en algún momento de `day`: episodios abiertos antes del fin del día
        y no cerrados antes de su comienzo. Vacío si el registro no cubre ese día (empezó después)."""
        d = day.isoformat()
        rows = self.db.conn.execute(
            "SELECT DISTINCT profile FROM scan_log WHERE kind = ? AND ticker = ? AND expiry = ? AND strike = ? AND long_strike IS ? "
            "AND substr(first_seen, 1, 10) <= ? AND (ended_at IS NULL OR substr(ended_at, 1, 10) >= ?)",
            (kind, ticker, expiry.isoformat(), strike, long_strike, d, d)).fetchall()
        return [r["profile"] for r in rows]

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
                          " price, yield_ann, dte, distance_pct, " + _EXTRA_COLS + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?," + _EXTRA_MARKS + ")",
                          (profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, at, at, h.price, h.yield_ann, h.price, h.yield_ann, h.dte,
                           h.distance_pct, *h.extras))
                self._event(at, "aparece", profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, None, h.price, h.yield_ann, h.dte,
                            h.distance_pct, h.extras)
            for row, h in kept:
                c.execute("UPDATE scan_log SET last_seen = ?, price = ?, yield_ann = ?, dte = ?, distance_pct = ?, " + _EXTRA_SET + " WHERE id = ?",
                          (at, h.price, h.yield_ann, h.dte, h.distance_pct, *h.extras, row.id))
            for row, reason in vanished:
                c.execute("UPDATE scan_log SET active = 0, ended_at = ?, end_reason = ? WHERE id = ?", (at, reason, row.id))
                self._event(at, "desaparece", profile, kind, row.ticker, row.expiry, row.strike, row.long_strike, reason, row.price, row.yield_ann,
                            row.dte, row.distance_pct, row.extras)
            c.execute("INSERT INTO scan_runs (at, profile, kind, found, appeared, vanished, skipped_tickers) VALUES (?,?,?,?,?,?,?)",
                      (at, profile, kind, len(appeared) + len(kept), len(appeared), len(vanished), skipped_tickers))

    def _event(self, at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct, extras) -> None:
        self.db.conn.execute("INSERT INTO scan_events (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte,"
                             " distance_pct, " + _EXTRA_COLS + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?," + _EXTRA_MARKS + ")",
                             (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct, *extras))

    # ---- lo que aún no está en el Google Sheet ------------------------------------------------
    def unsynced_events(self, limit: int = 5000) -> list:
        return self.db.conn.execute("SELECT * FROM scan_events WHERE synced = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()

    def unsynced_runs(self, limit: int = 5000) -> list:
        return self.db.conn.execute("SELECT * FROM scan_runs WHERE synced = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()

    def mark_synced(self, events: Sequence[int] = (), runs: Sequence[int] = ()) -> None:
        with self.db.conn:
            self.db.conn.executemany("UPDATE scan_events SET synced = 1 WHERE id = ?", [(i,) for i in events])
            self.db.conn.executemany("UPDATE scan_runs SET synced = 1 WHERE id = ?", [(i,) for i in runs])

    # ---- lo que muestra la pestaña Histórico (mismo contenido que el Google Sheet) ----------------
    def recent_events(self, limit: int = 500) -> list:
        return self.db.conn.execute("SELECT * FROM scan_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def recent_runs(self, limit: int = 500) -> list:
        return self.db.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def counts(self) -> dict[str, int]:
        c = self.db.conn
        return {"events": c.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0], "runs": c.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0],
                "active": c.execute("SELECT COUNT(*) FROM scan_log WHERE active = 1").fetchone()[0],
                "pending": c.execute("SELECT (SELECT COUNT(*) FROM scan_events WHERE synced = 0) + (SELECT COUNT(*) FROM scan_runs WHERE synced = 0)").fetchone()[0]}
