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
    # v5: pares clave/valor de la aplicación (p. ej. cuándo se hizo el último refresco completo)
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    # v6: cuándo se obtuvo el precio del subyacente
    "ALTER TABLE ticker_info ADD COLUMN price_at TEXT;",
    # v7: medias de los cierres diarios (tendencia alcista) y cuándo se calcularon
    """
    ALTER TABLE ticker_info ADD COLUMN sma_short REAL;
    ALTER TABLE ticker_info ADD COLUMN sma_long REAL;
    ALTER TABLE ticker_info ADD COLUMN trend_at TEXT;
    """,
    # v8: histórico de cierres diarios de los subyacentes (solo se piden los días que faltan)
    "CREATE TABLE daily_bars (ticker TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (ticker, day));",
    # v9: el histórico pasa de 400 días a ~2 años (730): se vacía para que la próxima actualización diaria lo descargue entero
    "DELETE FROM daily_bars;",
    # v10: cierre provisional del día en curso (último precio del refresco); el cierre oficial lo sustituye
    "ALTER TABLE daily_bars ADD COLUMN provisional INTEGER NOT NULL DEFAULT 0;",
    # v11: el histórico pasa de 730 a 1400 días (MA semanales/mensuales): se vacía para que se descargue entero
    "DELETE FROM daily_bars;",
    # v12: las medias SMA50/SMA200 guardadas ya no las usa nada (los filtros las calculan al escanear con daily_bars)
    """
    ALTER TABLE ticker_info DROP COLUMN sma_short;
    ALTER TABLE ticker_info DROP COLUMN sma_long;
    ALTER TABLE ticker_info DROP COLUMN trend_at;
    """,
    # v13: calidad de la empresa (EPS, trimestres positivos, capitalización, liquidez, resultados)
    """
    ALTER TABLE ticker_info ADD COLUMN eps_ttm REAL;
    ALTER TABLE ticker_info ADD COLUMN positive_quarters INTEGER;
    ALTER TABLE ticker_info ADD COLUMN reported_quarters INTEGER;
    ALTER TABLE ticker_info ADD COLUMN market_cap REAL;
    ALTER TABLE ticker_info ADD COLUMN option_liquidity INTEGER;
    ALTER TABLE ticker_info ADD COLUMN next_earnings TEXT;
    ALTER TABLE ticker_info ADD COLUMN eps_surprise_pct REAL;
    ALTER TABLE ticker_info ADD COLUMN fundamentals_at TEXT;
    """,
    # v14: apalancamiento y flujo de caja (SEC EDGAR)
    """
    ALTER TABLE ticker_info ADD COLUMN liabilities_to_equity REAL;
    ALTER TABLE ticker_info ADD COLUMN fcf_ttm REAL;
    ALTER TABLE ticker_info ADD COLUMN financials_end TEXT;
    ALTER TABLE ticker_info ADD COLUMN financials_at TEXT;
    """,
    # v15: los datos de calidad pasan a su propia tabla, para los tickers del Universo aunque no estén en la watchlist
    """
    CREATE TABLE ticker_quality (
        ticker TEXT PRIMARY KEY,
        eps_ttm REAL, positive_quarters INTEGER, reported_quarters INTEGER, market_cap REAL,
        option_liquidity INTEGER, next_earnings TEXT, eps_surprise_pct REAL, fundamentals_at TEXT,
        liabilities_to_equity REAL, fcf_ttm REAL, financials_end TEXT, financials_at TEXT
    );
    INSERT INTO ticker_quality
        SELECT ticker, eps_ttm, positive_quarters, reported_quarters, market_cap, option_liquidity, next_earnings, eps_surprise_pct, fundamentals_at, liabilities_to_equity, fcf_ttm, financials_end, financials_at FROM ticker_info
        WHERE fundamentals_at IS NOT NULL OR financials_at IS NOT NULL OR eps_ttm IS NOT NULL;
    ALTER TABLE ticker_info DROP COLUMN eps_ttm;
    ALTER TABLE ticker_info DROP COLUMN positive_quarters;
    ALTER TABLE ticker_info DROP COLUMN reported_quarters;
    ALTER TABLE ticker_info DROP COLUMN market_cap;
    ALTER TABLE ticker_info DROP COLUMN option_liquidity;
    ALTER TABLE ticker_info DROP COLUMN next_earnings;
    ALTER TABLE ticker_info DROP COLUMN eps_surprise_pct;
    ALTER TABLE ticker_info DROP COLUMN fundamentals_at;
    ALTER TABLE ticker_info DROP COLUMN liabilities_to_equity;
    ALTER TABLE ticker_info DROP COLUMN fcf_ttm;
    ALTER TABLE ticker_info DROP COLUMN financials_end;
    ALTER TABLE ticker_info DROP COLUMN financials_at;
    """,
    # v16: solvencia (deuda/patrimonio, cobertura de intereses, efectivo/deuda corriente, flujo operativo/deuda) y calidad
    # del flujo de caja (capex/flujo, FCF/activos, recompra neta). Se vuelve a consultar EDGAR para rellenarlas.
    """
    ALTER TABLE ticker_quality ADD COLUMN debt_to_equity REAL;
    ALTER TABLE ticker_quality ADD COLUMN interest_coverage REAL;
    ALTER TABLE ticker_quality ADD COLUMN cash_to_short_debt REAL;
    ALTER TABLE ticker_quality ADD COLUMN ocf_to_debt REAL;
    ALTER TABLE ticker_quality ADD COLUMN capex_to_ocf REAL;
    ALTER TABLE ticker_quality ADD COLUMN fcf_to_assets REAL;
    ALTER TABLE ticker_quality ADD COLUMN net_buyback_pct REAL;
    UPDATE ticker_quality SET financials_at = NULL;
    """,
    # v17: ROIC y estabilidad de los beneficios (años con pérdidas de los últimos 10 años fiscales). Se vuelve a consultar EDGAR.
    """
    ALTER TABLE ticker_quality ADD COLUMN roic REAL;
    ALTER TABLE ticker_quality ADD COLUMN loss_years INTEGER;
    ALTER TABLE ticker_quality ADD COLUMN fiscal_years INTEGER;
    UPDATE ticker_quality SET financials_at = NULL;
    """,
    # v18: ingresos crecientes (años con caída de ingresos) y estabilidad de los beneficios (volatilidad de su crecimiento).
    """
    ALTER TABLE ticker_quality ADD COLUMN revenue_drop_years INTEGER;
    ALTER TABLE ticker_quality ADD COLUMN revenue_years INTEGER;
    ALTER TABLE ticker_quality ADD COLUMN earnings_volatility REAL;
    UPDATE ticker_quality SET financials_at = NULL;
    """,
    # v19: HV30, IV30 y cambio de la IV a 5 días (de las market metrics de tastytrade, junto al IV Rank).
    """
    ALTER TABLE ticker_info ADD COLUMN hv30 REAL;
    ALTER TABLE ticker_info ADD COLUMN iv30 REAL;
    ALTER TABLE ticker_info ADD COLUMN iv_change_5d REAL;
    """,
    # v20: máximo y mínimo diarios (para el ATR). Los cierres ya guardados no los tienen: se rellenan al volver a pedir los últimos días.
    """
    ALTER TABLE daily_bars ADD COLUMN high REAL;
    ALTER TABLE daily_bars ADD COLUMN low REAL;
    """,
    # v21: cuándo se corrigió el bid size con el de TWS (el de tastytrade es entre 5 y 10 veces menor)
    """
    ALTER TABLE snapshots ADD COLUMN bid_size_tws_at TEXT;
    """,
    # v22: IV por vencimiento (JSON {fecha: IV}) de las market metrics de tastytrade, para el movimiento esperado
    """
    ALTER TABLE ticker_info ADD COLUMN expiry_ivs TEXT;
    """,
    # v23: alertas de la SEC (8-K de cuentas no fiables, cambio de auditor, exclusión de bolsa...) por ticker, en JSON
    """
    CREATE TABLE filing_alerts (
        ticker TEXT PRIMARY KEY,
        checked_at TEXT NOT NULL,
        alerts TEXT NOT NULL
    );
    """,
    # v24: cambios de valoración de analistas (Yahoo Finance) por ticker, en JSON
    """
    CREATE TABLE analyst_changes (
        ticker TEXT PRIMARY KEY,
        checked_at TEXT NOT NULL,
        changes TEXT NOT NULL
    );
    """,
    # v25: ficha informativa por empresa (descripción del negocio y noticias con fecha, automáticas y revisadas a mano)
    """
    CREATE TABLE company_notes (
        ticker TEXT PRIMARY KEY,
        description TEXT NOT NULL DEFAULT '',
        description_at TEXT,
        news TEXT NOT NULL DEFAULT '[]',
        news_at TEXT,
        summary TEXT,
        curated TEXT,
        curated_at TEXT
    );
    """,
    # v26: lista de puts que existen de cada ticker (la «cadena» de tastytrade), para no volver a pedirla tras un reinicio con el mercado cerrado
    """
    CREATE TABLE chain_cache (
        ticker TEXT PRIMARY KEY,
        fetched_at TEXT NOT NULL,
        data BLOB NOT NULL
    );
    """,
    # v27: historial de resultados (BPA real frente a estimación) y dividendo de cada empresa, para la ficha de los gráficos
    """
    CREATE TABLE company_history (
        ticker TEXT PRIMARY KEY,
        earnings_at TEXT,
        earnings TEXT,
        dividend_at TEXT,
        dividend TEXT
    );
    """,
    # v28: ingresos y beneficio neto de los últimos años fiscales (SEC EDGAR) para el gráfico de la ficha
    """
    ALTER TABLE company_history ADD COLUMN annual_at TEXT;
    ALTER TABLE company_history ADD COLUMN annual TEXT;
    """,
    # v29: EDGAR convierte a dólares las empresas que reportan en otra moneda (XP: reales) y Yahoo ya no deja resultados vacíos como definitivos:
    # se vuelve a consultar el balance de las fichas sin ningún dato financiero y se borran los resultados trimestrales vacíos
    """
    UPDATE ticker_quality SET financials_at = NULL
        WHERE liabilities_to_equity IS NULL AND fcf_ttm IS NULL AND debt_to_equity IS NULL AND roic IS NULL;
    UPDATE company_history SET earnings = NULL, earnings_at = NULL WHERE earnings = '[]';
    """,
    # v30: registro del escaneo automático de perfiles: un contrato (o spread) por perfil y «episodio» (aparece -> desaparece), sus eventos y cada pasada
    """
    CREATE TABLE scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile TEXT NOT NULL,
        kind TEXT NOT NULL,
        ticker TEXT NOT NULL,
        expiry TEXT NOT NULL,
        strike REAL NOT NULL,
        long_strike REAL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        ended_at TEXT,
        end_reason TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        first_price REAL, first_yield REAL,
        price REAL, yield_ann REAL, dte INTEGER, distance_pct REAL
    );
    CREATE INDEX idx_scan_log_active ON scan_log (profile, kind, active);
    CREATE TABLE scan_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        event TEXT NOT NULL,
        profile TEXT NOT NULL,
        kind TEXT NOT NULL,
        ticker TEXT NOT NULL,
        expiry TEXT NOT NULL,
        strike REAL NOT NULL,
        long_strike REAL,
        reason TEXT,
        price REAL, yield_ann REAL, dte INTEGER, distance_pct REAL,
        synced INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE scan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        profile TEXT NOT NULL,
        kind TEXT NOT NULL,
        found INTEGER NOT NULL,
        appeared INTEGER NOT NULL,
        vanished INTEGER NOT NULL,
        skipped_tickers INTEGER NOT NULL DEFAULT 0,
        synced INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v31: el registro guarda también crédito, riesgo, deltas, yield bid anual, IV Rank/Percentile, spread bid/ask y bid size
    """
    ALTER TABLE scan_log ADD COLUMN credit REAL;
    ALTER TABLE scan_log ADD COLUMN risk REAL;
    ALTER TABLE scan_log ADD COLUMN delta REAL;
    ALTER TABLE scan_log ADD COLUMN long_delta REAL;
    ALTER TABLE scan_log ADD COLUMN yield_bid REAL;
    ALTER TABLE scan_log ADD COLUMN iv_rank REAL;
    ALTER TABLE scan_log ADD COLUMN iv_pct REAL;
    ALTER TABLE scan_log ADD COLUMN spread_pct REAL;
    ALTER TABLE scan_log ADD COLUMN bid_size INTEGER;
    ALTER TABLE scan_events ADD COLUMN credit REAL;
    ALTER TABLE scan_events ADD COLUMN risk REAL;
    ALTER TABLE scan_events ADD COLUMN delta REAL;
    ALTER TABLE scan_events ADD COLUMN long_delta REAL;
    ALTER TABLE scan_events ADD COLUMN yield_bid REAL;
    ALTER TABLE scan_events ADD COLUMN iv_rank REAL;
    ALTER TABLE scan_events ADD COLUMN iv_pct REAL;
    ALTER TABLE scan_events ADD COLUMN spread_pct REAL;
    ALTER TABLE scan_events ADD COLUMN bid_size INTEGER;
    """,
    # v32: histórico de la |delta| real de las puts vendidas de la cartera, con el precio del subyacente al medirla
    """
    CREATE TABLE position_delta_log (
        at TEXT NOT NULL,
        key TEXT NOT NULL,
        ticker TEXT NOT NULL,
        delta REAL NOT NULL,
        underlying REAL,
        PRIMARY KEY (key, at)
    );
    """,
    # v33: el registro admite contratos de las fuentes de contratos (no solo escaneos): `origin` (`scan` o el nombre de la fuente), el trader que los abrió
    # y la fecha de apertura del propio contrato (`opened`; en la comunidad llega un día tarde, así que no es la de `first_seen`)
    """
    ALTER TABLE scan_log ADD COLUMN origin TEXT NOT NULL DEFAULT 'scan';
    ALTER TABLE scan_log ADD COLUMN trader TEXT;
    ALTER TABLE scan_log ADD COLUMN opened TEXT;
    ALTER TABLE scan_events ADD COLUMN origin TEXT NOT NULL DEFAULT 'scan';
    ALTER TABLE scan_events ADD COLUMN trader TEXT;
    ALTER TABLE scan_events ADD COLUMN opened TEXT;
    ALTER TABLE scan_runs ADD COLUMN origin TEXT NOT NULL DEFAULT 'scan';
    """,
    # v34: libro de operaciones compartidas del Feed de la comunidad (list_community_trades), una fila por operación (clave = su id del Feed),
    # con su ciclo apertura->cierre (status, closed, pnl), las patas y precios de apertura y cierre y las métricas de salida derivadas
    # (crédito de apertura, coste de cierre, % de prima capturado, DTE al abrir y al cerrar, días en posición). Se acumula aunque la tarjeta
    # salga de la ventana del Feed: así se puede analizar el historial de salida de cada trader siempre que la operación se viera una vez.
    """
    CREATE TABLE community_trades (
        id TEXT PRIMARY KEY,
        trader TEXT NOT NULL,
        ticker TEXT NOT NULL,
        strategy TEXT NOT NULL,
        currency TEXT,
        status TEXT NOT NULL,
        opened TEXT NOT NULL,
        closed TEXT,
        pnl REAL,
        note TEXT,
        legs TEXT,
        close_legs TEXT,
        credit_open REAL,
        close_cost REAL,
        pct_captured REAL,
        dte_open INTEGER,
        dte_close INTEGER,
        hold_days INTEGER,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_community_trades_trader ON community_trades (trader, status);
    """,
]


class Database:
    """Envoltorio fino sobre sqlite3. `path=':memory:'` para tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.quiet_changes = 0   # escrituras que no alteran los datos del Scanner (ver `MetaRepo.QUIET_KEYS`): no invalidan sus cachés
        self.path = str(path)   # dónde vive realmente esta base de datos (el Universo guarda sus ficheros a su lado)
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    @property
    def data_changes(self) -> int:
        """Escrituras acumuladas que SÍ pueden cambiar lo que escanea el Scanner (todas menos las periódicas de `meta` que no le afectan)."""
        return self.conn.total_changes - self.quiet_changes

    def migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS, start=1):
            if version > current:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {version}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
