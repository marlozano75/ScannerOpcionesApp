"""Repositorios sobre SQLite. Solo acceso a datos, sin lógica de negocio."""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.storage.db import Database


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _delete_not_in(db: Database, table: str, column: str, keep: Iterable[str]) -> int:
    keep = list(keep)
    with db.conn:
        if not keep:
            return db.conn.execute(f"DELETE FROM {table}").rowcount
        marks = ",".join("?" * len(keep))
        return db.conn.execute(f"DELETE FROM {table} WHERE {column} NOT IN ({marks})", keep).rowcount


class WatchlistRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, tickers: Iterable[str], now: datetime) -> list[str]:
        """Añade tickers; devuelve solo los nuevos (los ya existentes se ignoran)."""
        new: list[str] = []
        for t in tickers:
            cur = self.db.conn.execute(
                "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
                (t, now.isoformat()),
            )
            if cur.rowcount:
                new.append(t)
        self.db.conn.commit()
        return new

    def remove(self, ticker: str) -> None:
        with self.db.conn:
            self.db.conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))

    def list(self) -> list[str]:
        rows = self.db.conn.execute("SELECT ticker FROM watchlist ORDER BY ticker").fetchall()
        return [r["ticker"] for r in rows]

    def pending_daily_update(self, today: date) -> list[str]:
        """Tickers nunca actualizados o cuya última actualización diaria no es de hoy."""
        rows = self.db.conn.execute(
            "SELECT ticker FROM watchlist "
            "WHERE last_daily_update IS NULL OR substr(last_daily_update, 1, 10) < ? "
            "ORDER BY ticker",
            (today.isoformat(),),
        ).fetchall()
        return [r["ticker"] for r in rows]

    def mark_daily_updated(self, ticker: str, when: datetime) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE watchlist SET last_daily_update = ? WHERE ticker = ?",
                (when.isoformat(), ticker),
            )


class TickerInfoRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, info: TickerInfo) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO ticker_info (ticker, sector, category, underlying_price, "
                "days_to_ex_dividend, iv_rank, iv_percentile, updated_daily_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ticker) DO UPDATE SET sector=excluded.sector, "
                "category=excluded.category, underlying_price=excluded.underlying_price, "
                "days_to_ex_dividend=excluded.days_to_ex_dividend, iv_rank=excluded.iv_rank, "
                "iv_percentile=excluded.iv_percentile, updated_daily_at=excluded.updated_daily_at",
                (
                    info.ticker, info.sector, info.category, info.underlying_price,
                    info.days_to_ex_dividend, info.iv_rank, info.iv_percentile,
                    info.updated_daily_at.isoformat() if info.updated_daily_at else None,
                ),
            )

    def delete(self, ticker: str) -> None:
        with self.db.conn:
            self.db.conn.execute("DELETE FROM ticker_info WHERE ticker = ?", (ticker,))

    def update_iv_stats(self, ticker: str, iv_rank: Optional[float], iv_percentile: Optional[float]) -> None:
        """Actualiza IV Rank / Percentile (p. ej. con la IV en directo) sin tocar el resto."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ticker_info SET iv_rank = ?, iv_percentile = ? WHERE ticker = ?",
                (iv_rank, iv_percentile, ticker),
            )

    def update_price(self, ticker: str, price: float) -> None:
        """Actualiza solo el precio del subyacente (no toca la fecha de la actualización diaria)."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ticker_info SET underlying_price = ? WHERE ticker = ?", (price, ticker)
            )

    def purge_except(self, keep: Iterable[str]) -> int:
        """Borra la información de tickers que ya no están en la watchlist. Devuelve cuántos."""
        return _delete_not_in(self.db, "ticker_info", "ticker", keep)

    @staticmethod
    def _row(r) -> TickerInfo:
        return TickerInfo(
            ticker=r["ticker"], sector=r["sector"], category=r["category"],
            underlying_price=r["underlying_price"],
            days_to_ex_dividend=r["days_to_ex_dividend"],
            iv_rank=r["iv_rank"], iv_percentile=r["iv_percentile"],
            updated_daily_at=_dt(r["updated_daily_at"]),
        )

    def get(self, ticker: str) -> Optional[TickerInfo]:
        r = self.db.conn.execute("SELECT * FROM ticker_info WHERE ticker = ?", (ticker,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> dict[str, TickerInfo]:
        rows = self.db.conn.execute("SELECT * FROM ticker_info").fetchall()
        return {r["ticker"]: self._row(r) for r in rows}


class IVHistoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def last_day(self, ticker: str) -> Optional[date]:
        """Último día guardado; permite descargar solo los días nuevos."""
        r = self.db.conn.execute(
            "SELECT MAX(day) AS d FROM iv_history WHERE ticker = ?", (ticker,)
        ).fetchone()
        return date.fromisoformat(r["d"]) if r["d"] else None

    def add(self, ticker: str, points: Iterable[tuple]) -> int:
        """Guarda barras diarias. Cada punto es (día, cierre) o (día, cierre, máximo, mínimo)."""
        rows = []
        for p in points:
            day, close = p[0], p[1]
            high = p[2] if len(p) > 2 else None
            low = p[3] if len(p) > 3 else None
            rows.append((ticker, day.isoformat(), close, high, low))
        with self.db.conn:
            cur = self.db.conn.executemany(
                "INSERT OR REPLACE INTO iv_history (ticker, day, iv, high, low) VALUES (?,?,?,?,?)", rows
            )
        return cur.rowcount

    def bars(self, ticker: str, since: Optional[date] = None) -> list[tuple]:
        """Barras (día, cierre, máximo, mínimo); máximo/mínimo son None en barras antiguas."""
        sql = "SELECT day, iv, high, low FROM iv_history WHERE ticker = ?"
        params: list = [ticker]
        if since is not None:
            sql += " AND day >= ?"
            params.append(since.isoformat())
        rows = self.db.conn.execute(sql + " ORDER BY day", params).fetchall()
        return [(date.fromisoformat(r["day"]), r["iv"], r["high"], r["low"]) for r in rows]

    def needs_hilo_backfill(self, ticker: str, since: date) -> bool:
        """True si hay barras en la ventana sin máximo/mínimo (guardadas antes de la migración v3)."""
        r = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM iv_history WHERE ticker = ? AND day >= ? AND (high IS NULL OR low IS NULL)",
            (ticker, since.isoformat()),
        ).fetchone()
        return r["n"] > 0

    def series(self, ticker: str, since: Optional[date] = None) -> list[tuple[date, float]]:
        sql = "SELECT day, iv FROM iv_history WHERE ticker = ?"
        params: list = [ticker]
        if since is not None:
            sql += " AND day >= ?"
            params.append(since.isoformat())
        rows = self.db.conn.execute(sql + " ORDER BY day", params).fetchall()
        return [(date.fromisoformat(r["day"]), r["iv"]) for r in rows]

    def prune(self, ticker: str, before: date) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "DELETE FROM iv_history WHERE ticker = ? AND day < ?", (ticker, before.isoformat())
            )


class ContractRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def replace_for_ticker(self, ticker: str, contracts: Iterable[OptionContract]) -> None:
        """Sustituye los contratos candidatos del ticker (los snapshots se borran en cascada)."""
        with self.db.conn:
            self.db.conn.execute("DELETE FROM contracts WHERE ticker = ?", (ticker,))
            self.db.conn.executemany(
                "INSERT OR IGNORE INTO contracts (ticker, expiry, strike, right, multiplier, con_id) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (c.ticker, c.expiry.isoformat(), c.strike, c.right.value, c.multiplier, c.con_id)
                    for c in contracts
                ],
            )

    @staticmethod
    def key(c: OptionContract) -> tuple:
        return (c.expiry.isoformat(), c.strike, c.right.value)

    def sync_for_ticker(
        self, ticker: str, wanted: set[tuple], new: Iterable[OptionContract]
    ) -> tuple[int, int]:
        """Sincroniza los contratos del ticker SIN reconstruirlos: borra los que ya no están en
        `wanted` (vencidos o fuera de la ventana) e inserta `new`. Los que siguen conservan su
        snapshot. `wanted` son claves `key(c)`. Devuelve (borrados, insertados)."""
        with self.db.conn:
            rows = self.db.conn.execute(
                "SELECT id, expiry, strike, right FROM contracts WHERE ticker = ?", (ticker,)
            ).fetchall()
            stale = [r["id"] for r in rows if (r["expiry"], r["strike"], r["right"]) not in wanted]
            self.db.conn.executemany("DELETE FROM contracts WHERE id = ?", [(i,) for i in stale])
            inserted = self.db.conn.executemany(
                "INSERT OR IGNORE INTO contracts (ticker, expiry, strike, right, multiplier, con_id) "
                "VALUES (?,?,?,?,?,?)",
                [(c.ticker, c.expiry.isoformat(), c.strike, c.right.value, c.multiplier, c.con_id) for c in new],
            ).rowcount
        return len(stale), max(inserted, 0)

    def keys(self, ticker: str) -> set[tuple]:
        rows = self.db.conn.execute(
            "SELECT expiry, strike, right FROM contracts WHERE ticker = ?", (ticker,)
        ).fetchall()
        return {(r["expiry"], r["strike"], r["right"]) for r in rows}

    # combinaciones que IBKR no lista: se recuerdan para no volver a validarlas cada día
    def miss_keys(self, ticker: str) -> set[tuple]:
        rows = self.db.conn.execute(
            "SELECT expiry, strike, right FROM contract_misses WHERE ticker = ?", (ticker,)
        ).fetchall()
        return {(r["expiry"], r["strike"], r["right"]) for r in rows}

    def add_misses(self, ticker: str, misses: Iterable[OptionContract]) -> None:
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT OR IGNORE INTO contract_misses (ticker, expiry, strike, right) VALUES (?,?,?,?)",
                [(ticker, c.expiry.isoformat(), c.strike, c.right.value) for c in misses],
            )

    def clear_misses(self, ticker: str) -> None:
        with self.db.conn:
            self.db.conn.execute("DELETE FROM contract_misses WHERE ticker = ?", (ticker,))

    def purge_expired_misses(self, today: date) -> int:
        with self.db.conn:
            return self.db.conn.execute(
                "DELETE FROM contract_misses WHERE expiry < ?", (today.isoformat(),)
            ).rowcount

    def delete_for_ticker(self, ticker: str) -> int:
        """Borra los contratos del ticker (sus snapshots caen en cascada). Devuelve cuántos."""
        with self.db.conn:
            self.db.conn.execute("DELETE FROM contract_misses WHERE ticker = ?", (ticker,))
            return self.db.conn.execute("DELETE FROM contracts WHERE ticker = ?", (ticker,)).rowcount

    def purge_except(self, keep: Iterable[str]) -> int:
        """Borra los contratos de tickers que ya no están en la watchlist. Devuelve cuántos."""
        _delete_not_in(self.db, "contract_misses", "ticker", keep)
        return _delete_not_in(self.db, "contracts", "ticker", keep)

    def list(self, ticker: Optional[str] = None) -> list[OptionContract]:
        sql = "SELECT * FROM contracts"
        params: tuple = ()
        if ticker:
            sql += " WHERE ticker = ?"
            params = (ticker,)
        rows = self.db.conn.execute(sql + " ORDER BY ticker, expiry, strike", params).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> OptionContract:
        return OptionContract(
            ticker=r["ticker"], expiry=date.fromisoformat(r["expiry"]), strike=r["strike"],
            right=OptionRight(r["right"]), multiplier=r["multiplier"], con_id=r["con_id"],
        )


class SnapshotRepo:
    """Último snapshot por contrato."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, snap: ContractSnapshot) -> bool:
        """Guarda el snapshot. False si el contrato no existe en `contracts`."""
        c = snap.contract
        r = self.db.conn.execute(
            "SELECT id FROM contracts WHERE ticker=? AND expiry=? AND strike=? AND right=?",
            (c.ticker, c.expiry.isoformat(), c.strike, c.right.value),
        ).fetchone()
        if r is None:
            return False
        with self.db.conn:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO snapshots (contract_id, updated_at, bid, ask, last, delta, iv, "
                "open_interest, spread_pct, yield_pct, yield_annualized_pct, iv_rank, iv_percentile, "
                "initial_margin, bid_size, margin_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["id"], snap.updated_at.isoformat(), snap.bid, snap.ask, snap.last, snap.delta,
                    snap.iv, snap.open_interest, snap.spread_pct, snap.yield_pct,
                    snap.yield_annualized_pct, snap.iv_rank, snap.iv_percentile, snap.initial_margin, snap.bid_size,
                    snap.margin_at.isoformat() if snap.margin_at else None,
                ),
            )
        return True

    def all(self, ticker: Optional[str] = None) -> list[ContractSnapshot]:
        sql = (
            "SELECT c.*, s.* FROM snapshots s JOIN contracts c ON c.id = s.contract_id"
        )
        params: tuple = ()
        if ticker:
            sql += " WHERE c.ticker = ?"
            params = (ticker,)
        rows = self.db.conn.execute(sql + " ORDER BY c.ticker, c.expiry, c.strike", params).fetchall()
        return [
            ContractSnapshot(
                contract=ContractRepo._row(r), updated_at=datetime.fromisoformat(r["updated_at"]),
                bid=r["bid"], ask=r["ask"], last=r["last"], delta=r["delta"], iv=r["iv"],
                open_interest=r["open_interest"], spread_pct=r["spread_pct"], yield_pct=r["yield_pct"],
                yield_annualized_pct=r["yield_annualized_pct"], iv_rank=r["iv_rank"],
                iv_percentile=r["iv_percentile"], initial_margin=r["initial_margin"], bid_size=r["bid_size"],
                margin_at=_dt(r["margin_at"]),
            )
            for r in rows
        ]
