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
    trader: Optional[str] = None           # quién abrió el contrato (solo fuentes de contratos)
    opened: Optional[str] = None           # fecha ISO en que se abrió el contrato (solo fuentes de contratos)

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0, self.trader or "")

    @property
    def extras(self) -> tuple:
        return tuple(getattr(self, f) for f in EXTRA_FIELDS)


SCAN = "scan"      # `origin` de los episodios que salen del escaneo de perfiles; los de una fuente de contratos llevan el nombre de la fuente

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
    origin: str = SCAN
    trader: Optional[str] = None
    opened: Optional[str] = None

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiry, self.strike, self.long_strike or 0.0, self.trader or "")

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
                     r["distance_pct"], *(r[f] for f in EXTRA_FIELDS), r["origin"], r["trader"], r["opened"])


def _filters(origin: Optional[str], trader: Optional[str]) -> tuple[str, tuple]:
    """Condiciones opcionales por origen y trader (vacío = sin filtro)."""
    where, args = "", []
    if origin:
        where, args = where + " AND origin = ?", [*args, origin]
    if trader:
        where, args = where + " AND trader = ?", [*args, trader]
    return where, tuple(args)


class ScanLogRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def active(self, profile: str, kind: str, origin: str = SCAN) -> list[LoggedRow]:
        rows = self.db.conn.execute("SELECT * FROM scan_log WHERE origin = ? AND profile = ? AND kind = ? AND active = 1", (origin, profile, kind)).fetchall()
        return [_row(r) for r in rows]

    def active_groups(self, origin: str) -> list[tuple[str, str]]:
        """(perfil, tipo) con algún episodio abierto de ese origen."""
        rows = self.db.conn.execute("SELECT DISTINCT profile, kind FROM scan_log WHERE origin = ? AND active = 1", (origin,)).fetchall()
        return [(r["profile"], r["kind"]) for r in rows]

    def profiles_showing(self, kind: str, ticker: str, expiry: date, strike: float, long_strike: Optional[float], day: date) -> list[str]:
        """Perfiles cuyo escaneo mostraba ese contrato (o spread, con `long_strike`) en algún momento de `day`: episodios abiertos antes del fin del día
        y no cerrados antes de su comienzo. Vacío si el registro no cubre ese día (empezó después). Solo el escaneo de perfiles."""
        d = day.isoformat()
        rows = self.db.conn.execute(
            "SELECT DISTINCT profile FROM scan_log WHERE origin = 'scan' AND kind = ? AND ticker = ? AND expiry = ? AND strike = ? AND long_strike IS ? "
            "AND substr(first_seen, 1, 10) <= ? AND (ended_at IS NULL OR substr(ended_at, 1, 10) >= ?)",
            (kind, ticker, expiry.isoformat(), strike, long_strike, d, d)).fetchall()
        return [r["profile"] for r in rows]

    def all_active(self, origin: Optional[str] = None, trader: Optional[str] = None) -> list[LoggedRow]:
        where, args = _filters(origin, trader)
        rows = self.db.conn.execute(f"SELECT * FROM scan_log WHERE active = 1{where} ORDER BY origin, profile, kind, ticker, expiry, strike", args).fetchall()
        return [_row(r) for r in rows]

    def apply(self, at: str, profile: str, kind: str, appeared: Sequence[Hit], kept: Sequence[tuple[LoggedRow, Hit]],
              vanished: Sequence[tuple[LoggedRow, str]], skipped_tickers: int, origin: str = SCAN) -> None:
        """Una pasada en una sola transacción: abre los episodios nuevos, actualiza los que siguen, cierra los que se fueron (con su motivo) y
        anota la pasada. `origin`: `scan` (escaneo de perfiles) o el nombre de la fuente de contratos (entonces `profile` es el perfil de escaneo
        al que encajan hoy esos contratos, o «Sin perfil»)."""
        with self.db.conn:
            c = self.db.conn
            for h in appeared:
                c.execute("INSERT INTO scan_log (profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, first_price, first_yield,"
                          " price, yield_ann, dte, distance_pct, " + _EXTRA_COLS + ", origin, trader, opened) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?," + _EXTRA_MARKS + ",?,?,?)",
                          (profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, at, at, h.price, h.yield_ann, h.price, h.yield_ann, h.dte,
                           h.distance_pct, *h.extras, origin, h.trader, h.opened))
                self._event(at, "aparece", profile, kind, h.ticker, h.expiry, h.strike, h.long_strike, None, h.price, h.yield_ann, h.dte,
                            h.distance_pct, h.extras, origin, h.trader, h.opened)
            for row, h in kept:
                c.execute("UPDATE scan_log SET last_seen = ?, price = ?, yield_ann = ?, dte = ?, distance_pct = ?, " + _EXTRA_SET + " WHERE id = ?",
                          (at, h.price, h.yield_ann, h.dte, h.distance_pct, *h.extras, row.id))
            for row, reason in vanished:
                c.execute("UPDATE scan_log SET active = 0, ended_at = ?, end_reason = ? WHERE id = ?", (at, reason, row.id))
                self._event(at, "desaparece", profile, kind, row.ticker, row.expiry, row.strike, row.long_strike, reason, row.price, row.yield_ann,
                            row.dte, row.distance_pct, row.extras, origin, row.trader, row.opened)
            c.execute("INSERT INTO scan_runs (at, profile, kind, found, appeared, vanished, skipped_tickers, origin) VALUES (?,?,?,?,?,?,?,?)",
                      (at, profile, kind, len(appeared) + len(kept), len(appeared), len(vanished), skipped_tickers, origin))

    def _event(self, at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct, extras,
               origin=SCAN, trader=None, opened=None) -> None:
        self.db.conn.execute("INSERT INTO scan_events (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte,"
                             " distance_pct, " + _EXTRA_COLS + ", origin, trader, opened) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?," + _EXTRA_MARKS + ",?,?,?)",
                             (at, event, profile, kind, ticker, expiry, strike, long_strike, reason, price, yield_ann, dte, distance_pct, *extras,
                              origin, trader, opened))

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
    def recent_events(self, limit: int = 500, origin: Optional[str] = None, trader: Optional[str] = None) -> list:
        where, args = _filters(origin, trader)
        return self.db.conn.execute(f"SELECT * FROM scan_events WHERE 1 = 1{where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()

    def recent_runs(self, limit: int = 500, origin: Optional[str] = None) -> list:
        where, args = _filters(origin, None)
        return self.db.conn.execute(f"SELECT * FROM scan_runs WHERE 1 = 1{where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()

    def origins(self) -> list[str]:
        """Orígenes que hay en el registro (`scan` y las fuentes de contratos), para el filtro de la pestaña Histórico."""
        return [r[0] for r in self.db.conn.execute("SELECT DISTINCT origin FROM scan_events UNION SELECT DISTINCT origin FROM scan_log ORDER BY 1").fetchall()]

    def traders(self) -> list[str]:
        return [r[0] for r in self.db.conn.execute(
            "SELECT DISTINCT trader FROM scan_events WHERE trader IS NOT NULL UNION SELECT DISTINCT trader FROM scan_log WHERE trader IS NOT NULL ORDER BY 1 COLLATE NOCASE").fetchall()]

    def counts(self) -> dict[str, int]:
        c = self.db.conn
        return {"events": c.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0], "runs": c.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0],
                "active": c.execute("SELECT COUNT(*) FROM scan_log WHERE active = 1").fetchone()[0],
                "pending": c.execute("SELECT (SELECT COUNT(*) FROM scan_events WHERE synced = 0) + (SELECT COUNT(*) FROM scan_runs WHERE synced = 0)").fetchone()[0]}
