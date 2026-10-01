"""Conexión SQLite y migraciones simples basadas en PRAGMA user_version."""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE watchlist (
        ticker TEXT PRIMARY KEY,
        added_at TEXT NOT NULL,
        last_daily_update TEXT
    );
    CREATE TABLE ticker_info (
        ticker TEXT PRIMARY KEY,
        sector TEXT,
        category TEXT,
        underlying_price REAL,
        days_to_ex_dividend INTEGER,
        iv_rank REAL,
        iv_percentile REAL,
        updated_daily_at TEXT
    );
    CREATE TABLE iv_history (
        ticker TEXT NOT NULL,
        day TEXT NOT NULL,
        iv REAL NOT NULL,
        PRIMARY KEY (ticker, day)
    );
    CREATE TABLE contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        expiry TEXT NOT NULL,
        strike REAL NOT NULL,
        right TEXT NOT NULL,
        multiplier INTEGER NOT NULL,
        con_id INTEGER,
        UNIQUE (ticker, expiry, strike, right)
    );
    CREATE INDEX idx_contracts_ticker ON contracts (ticker);
    CREATE TABLE snapshots (
        contract_id INTEGER PRIMARY KEY REFERENCES contracts(id) ON DELETE CASCADE,
        updated_at TEXT NOT NULL,
        bid REAL, ask REAL, last REAL, delta REAL, iv REAL,
        open_interest INTEGER,
        spread_pct REAL, yield_pct REAL, yield_annualized_pct REAL,
        iv_rank REAL, iv_percentile REAL,
        initial_margin REAL
    );
    """,
    # v2: tamaño del bid
    "ALTER TABLE snapshots ADD COLUMN bid_size INTEGER;",
    # v3: máximo y mínimo diarios de la IV (para el rango del IV Rank)
    "ALTER TABLE iv_history ADD COLUMN high REAL; ALTER TABLE iv_history ADD COLUMN low REAL;",
    # v4: combinaciones strike/vencimiento que IBKR no lista (no se revalidan cada día) y fecha del margen
    """
    CREATE TABLE contract_misses (
        ticker TEXT NOT NULL,
        expiry TEXT NOT NULL,
        strike REAL NOT NULL,
        right TEXT NOT NULL,
        PRIMARY KEY (ticker, expiry, strike, right)
    );
    ALTER TABLE snapshots ADD COLUMN margin_at TEXT;
    """,
]


class Database:
    """Envoltorio fino sobre sqlite3. `path=':memory:'` para tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS, start=1):
            if version > current:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {version}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
