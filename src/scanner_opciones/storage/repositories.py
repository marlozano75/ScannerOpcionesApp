"""Repositorios sobre SQLite. Solo acceso a datos, sin lógica de negocio."""
from __future__ import annotations

import bisect
import json
import zlib
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.marketdata.analysts import AnalystChange
from scanner_opciones.marketdata.company import CompanyNotes, NewsItem
from scanner_opciones.marketdata.filings import FilingAlert
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


class MetaRepo:
    """Valores sueltos de la aplicación que deben sobrevivir a un reinicio."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> Optional[str]:
        r = self.db.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r["value"] if r else None

    # copias que se reescriben cada minuto (cuenta y VIX) y no alteran lo que escanea el Scanner: no deben invalidar sus cachés
    QUIET_KEYS = frozenset({"portfolio_cache", "vix_cache"})

    def set(self, key: str, value: str) -> None:
        before = self.db.conn.total_changes
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        if key in self.QUIET_KEYS:
            self.db.quiet_changes += self.db.conn.total_changes - before


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

    _COLUMNS = (
        "ticker", "sector", "category", "underlying_price", "days_to_ex_dividend", "iv_rank", "iv_percentile",
        "updated_daily_at", "price_at", "hv30", "iv30", "iv_change_5d", "expiry_ivs",
    )
    # la ficha se lee con los datos de calidad (tabla aparte, también para tickers que no están en la watchlist)
    _SELECT = (
        "SELECT t.*, q.eps_ttm, q.positive_quarters, q.reported_quarters, q.market_cap, q.option_liquidity, "
        "q.next_earnings, q.eps_surprise_pct, q.fundamentals_at, q.liabilities_to_equity, q.fcf_ttm, "
        "q.financials_end, q.financials_at, q.debt_to_equity, q.interest_coverage, q.cash_to_short_debt, q.ocf_to_debt, q.capex_to_ocf, q.fcf_to_assets, q.net_buyback_pct, q.roic, q.loss_years, q.fiscal_years, q.revenue_drop_years, q.revenue_years, q.earnings_volatility FROM ticker_info t LEFT JOIN ticker_quality q ON q.ticker = t.ticker"
    )

    def upsert(self, info: TickerInfo) -> None:
        """Guarda la ficha. Los campos de calidad de `info` se ignoran: los guarda `QualityRepo`."""
        cols = self._COLUMNS
        values = (
            info.ticker, info.sector, info.category, info.underlying_price, info.days_to_ex_dividend,
            info.iv_rank, info.iv_percentile,
            info.updated_daily_at.isoformat() if info.updated_daily_at else None,
            info.price_at.isoformat() if info.price_at else None,
            info.hv30, info.iv30, info.iv_change_5d, json.dumps(info.expiry_ivs) if info.expiry_ivs else None,
        )
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
        with self.db.conn:
            self.db.conn.execute(
                f"INSERT INTO ticker_info ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(ticker) DO UPDATE SET {updates}",
                values,
            )

    def delete(self, ticker: str) -> None:
        with self.db.conn:
            self.db.conn.execute("DELETE FROM ticker_info WHERE ticker = ?", (ticker,))

    def update_iv_stats(
        self, ticker: str, iv_rank: Optional[float], iv_percentile: Optional[float],
        hv30: Optional[float] = None, iv30: Optional[float] = None, iv_change_5d: Optional[float] = None,
        expiry_ivs: Optional[dict] = None,
    ) -> None:
        """Actualiza IV Rank / Percentile (p. ej. con la IV en directo) sin tocar el resto. HV30, IV30 y el cambio de la
        IV a 5 días solo se pisan si llegan (un dato que falta no borra el último guardado)."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ticker_info SET iv_rank = ?, iv_percentile = ?, hv30 = COALESCE(?, hv30), iv30 = COALESCE(?, iv30), "
                "iv_change_5d = COALESCE(?, iv_change_5d), expiry_ivs = COALESCE(?, expiry_ivs) WHERE ticker = ?",
                (iv_rank, iv_percentile, hv30, iv30, iv_change_5d, json.dumps(expiry_ivs) if expiry_ivs else None, ticker),
            )

    def update_sector(self, ticker: str, sector: Optional[str], category: Optional[str]) -> None:
        """Completa el sector y la categoría (p. ej. cuando TWS vuelve tras actualizar sin él)."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ticker_info SET sector = ?, category = ? WHERE ticker = ?", (sector, category, ticker))

    def update_price(self, ticker: str, price: float, when: datetime) -> None:
        """Actualiza solo el precio del subyacente y su fecha (no toca la actualización diaria)."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ticker_info SET underlying_price = ?, price_at = ? WHERE ticker = ?",
                (price, when.isoformat(), ticker),
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
            price_at=_dt(r["price_at"]),
            eps_ttm=r["eps_ttm"], positive_quarters=r["positive_quarters"], reported_quarters=r["reported_quarters"],
            market_cap=r["market_cap"], option_liquidity=r["option_liquidity"],
            next_earnings=date.fromisoformat(r["next_earnings"]) if r["next_earnings"] else None,
            eps_surprise_pct=r["eps_surprise_pct"], fundamentals_at=_dt(r["fundamentals_at"]),
            liabilities_to_equity=r["liabilities_to_equity"], fcf_ttm=r["fcf_ttm"],
            financials_end=date.fromisoformat(r["financials_end"]) if r["financials_end"] else None,
            financials_at=_dt(r["financials_at"]),
            debt_to_equity=r["debt_to_equity"],
            interest_coverage=r["interest_coverage"],
            cash_to_short_debt=r["cash_to_short_debt"],
            ocf_to_debt=r["ocf_to_debt"],
            capex_to_ocf=r["capex_to_ocf"],
            fcf_to_assets=r["fcf_to_assets"],
            net_buyback_pct=r["net_buyback_pct"],
            roic=r["roic"], loss_years=r["loss_years"], fiscal_years=r["fiscal_years"],
            revenue_drop_years=r["revenue_drop_years"], revenue_years=r["revenue_years"],
            earnings_volatility=r["earnings_volatility"],
            hv30=r["hv30"], iv30=r["iv30"], iv_change_5d=r["iv_change_5d"],
            expiry_ivs=json.loads(r["expiry_ivs"]) if r["expiry_ivs"] else None,
        )

    def get(self, ticker: str) -> Optional[TickerInfo]:
        r = self.db.conn.execute(self._SELECT + " WHERE t.ticker = ?", (ticker,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> dict[str, TickerInfo]:
        rows = self.db.conn.execute(self._SELECT).fetchall()
        return {r["ticker"]: self._row(r) for r in rows}


class FilingAlertRepo:
    """Alertas de presentaciones de la SEC por ticker (lista vacía = consultado y sin alertas)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, found: dict[str, list[FilingAlert]], checked_at: datetime) -> int:
        rows = [(t, checked_at.isoformat(), json.dumps([a.to_json() for a in alerts])) for t, alerts in found.items()]
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO filing_alerts (ticker, checked_at, alerts) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET checked_at = excluded.checked_at, alerts = excluded.alerts", rows)
        return len(rows)

    def all(self) -> dict[str, tuple[datetime, list[FilingAlert]]]:
        out = {}
        for r in self.db.conn.execute("SELECT ticker, checked_at, alerts FROM filing_alerts"):
            out[r["ticker"]] = (datetime.fromisoformat(r["checked_at"]), [FilingAlert.from_json(a) for a in json.loads(r["alerts"])])
        return out

    def purge_except(self, keep: Iterable[str]) -> int:
        keep = list(keep)
        with self.db.conn:
            if not keep:
                return self.db.conn.execute("DELETE FROM filing_alerts").rowcount
            marks = ",".join("?" * len(keep))
            return self.db.conn.execute(f"DELETE FROM filing_alerts WHERE ticker NOT IN ({marks})", keep).rowcount


class AnalystRepo:
    """Cambios de valoración de analistas por ticker (lista vacía = consultado y sin cambios en la ventana)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, found: dict[str, list[AnalystChange]], checked_at: datetime) -> int:
        rows = [(t, checked_at.isoformat(), json.dumps([c.to_json() for c in changes])) for t, changes in found.items()]
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO analyst_changes (ticker, checked_at, changes) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET checked_at = excluded.checked_at, changes = excluded.changes", rows)
        return len(rows)

    def all(self) -> dict[str, tuple[datetime, list[AnalystChange]]]:
        out = {}
        for r in self.db.conn.execute("SELECT ticker, checked_at, changes FROM analyst_changes"):
            out[r["ticker"]] = (datetime.fromisoformat(r["checked_at"]), [AnalystChange.from_json(c) for c in json.loads(r["changes"])])
        return out

    def purge_except(self, keep: Iterable[str]) -> int:
        keep = list(keep)
        with self.db.conn:
            if not keep:
                return self.db.conn.execute("DELETE FROM analyst_changes").rowcount
            marks = ",".join("?" * len(keep))
            return self.db.conn.execute(f"DELETE FROM analyst_changes WHERE ticker NOT IN ({marks})", keep).rowcount


class CompanyNotesRepo:
    """Descripción del negocio y noticias con fecha por ticker. Las columnas automáticas (`description`, `news`) y las escritas a mano
    (`summary`, `curated`) se guardan por separado: una pasada automática nunca pisa lo revisado."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save_descriptions(self, found: dict[str, str], at: date) -> int:
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO company_notes (ticker, description, description_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET description = excluded.description, description_at = excluded.description_at",
                [(t, d, at.isoformat()) for t, d in found.items()])
        return len(found)

    def save_news(self, found: dict[str, list[NewsItem]], at: date) -> int:
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO company_notes (ticker, news, news_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET news = excluded.news, news_at = excluded.news_at",
                [(t, json.dumps([n.to_json() for n in items]), at.isoformat()) for t, items in found.items()])
        return len(found)

    def save_curated(self, ticker: str, summary: Optional[str], curated: Optional[list[NewsItem]], at: date) -> None:
        """Resumen y noticias revisadas a mano; `None` deja lo que hubiera."""
        with self.db.conn:
            self.db.conn.execute("INSERT INTO company_notes (ticker) VALUES (?) ON CONFLICT(ticker) DO NOTHING", (ticker,))
            if summary is not None:
                self.db.conn.execute("UPDATE company_notes SET summary = ? WHERE ticker = ?", (summary or None, ticker))
            if curated is not None:
                self.db.conn.execute("UPDATE company_notes SET curated = ?, curated_at = ? WHERE ticker = ?",
                                     (json.dumps([n.to_json() for n in curated]) if curated else None, at.isoformat(), ticker))

    def all(self) -> dict[str, CompanyNotes]:
        def day(v):
            return date.fromisoformat(v[:10]) if v else None
        out = {}
        for r in self.db.conn.execute("SELECT * FROM company_notes"):
            out[r["ticker"]] = CompanyNotes(
                r["description"] or "", day(r["description_at"]), tuple(NewsItem.from_json(n) for n in json.loads(r["news"] or "[]")),
                day(r["news_at"]), r["summary"] or "", tuple(NewsItem.from_json(n) for n in json.loads(r["curated"] or "[]")), day(r["curated_at"]))
        return out

    def purge_except(self, keep: Iterable[str]) -> int:
        """Borra las fichas automáticas de lo que ya no se sigue; lo escrito a mano (resumen o noticias revisadas) se conserva."""
        keep = list(keep)
        with self.db.conn:
            if not keep:
                return self.db.conn.execute("DELETE FROM company_notes WHERE summary IS NULL AND curated IS NULL").rowcount
            marks = ",".join("?" * len(keep))
            return self.db.conn.execute(
                f"DELETE FROM company_notes WHERE ticker NOT IN ({marks}) AND summary IS NULL AND curated IS NULL", keep).rowcount


class QualityRepo:
    """Datos de calidad de la empresa (beneficios, trimestres, capitalización, liquidez, resultados, pasivo/patrimonio y
    flujo de caja) por ticker, para todo el Universo y no solo para la watchlist. Se guardan y se leen como `TickerInfo`
    con solo esos campos rellenos."""

    COLUMNS = (
        "eps_ttm", "positive_quarters", "reported_quarters", "market_cap", "option_liquidity", "next_earnings",
        "eps_surprise_pct", "fundamentals_at", "liabilities_to_equity", "fcf_ttm", "financials_end", "financials_at",
        "debt_to_equity", "interest_coverage", "cash_to_short_debt", "ocf_to_debt", "capex_to_ocf", "fcf_to_assets",
        "net_buyback_pct", "roic", "loss_years", "fiscal_years", "revenue_drop_years", "revenue_years", "earnings_volatility",
    )

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _iso(value) -> Optional[str]:
        return value.isoformat() if value is not None else None

    def save(self, infos: Iterable[TickerInfo]) -> int:
        """Guarda (crea o sustituye) la fila de calidad de cada ticker, en una sola transacción."""
        cols = ("ticker", *self.COLUMNS)
        rows = [
            (i.ticker, i.eps_ttm, i.positive_quarters, i.reported_quarters, i.market_cap, i.option_liquidity,
             self._iso(i.next_earnings), i.eps_surprise_pct, self._iso(i.fundamentals_at), i.liabilities_to_equity,
             i.fcf_ttm, self._iso(i.financials_end), self._iso(i.financials_at), i.debt_to_equity, i.interest_coverage,
             i.cash_to_short_debt, i.ocf_to_debt, i.capex_to_ocf, i.fcf_to_assets, i.net_buyback_pct, i.roic, i.loss_years, i.fiscal_years, i.revenue_drop_years, i.revenue_years, i.earnings_volatility)
            for i in infos
        ]
        updates = ", ".join(f"{c}=excluded.{c}" for c in self.COLUMNS)
        with self.db.conn:
            self.db.conn.executemany(
                f"INSERT INTO ticker_quality ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(ticker) DO UPDATE SET {updates}", rows)
        return len(rows)

    @staticmethod
    def _row(r) -> TickerInfo:
        return TickerInfo(
            ticker=r["ticker"], eps_ttm=r["eps_ttm"], positive_quarters=r["positive_quarters"],
            reported_quarters=r["reported_quarters"], market_cap=r["market_cap"], option_liquidity=r["option_liquidity"],
            next_earnings=date.fromisoformat(r["next_earnings"]) if r["next_earnings"] else None,
            eps_surprise_pct=r["eps_surprise_pct"], fundamentals_at=_dt(r["fundamentals_at"]),
            liabilities_to_equity=r["liabilities_to_equity"], fcf_ttm=r["fcf_ttm"],
            financials_end=date.fromisoformat(r["financials_end"]) if r["financials_end"] else None,
            financials_at=_dt(r["financials_at"]),
            debt_to_equity=r["debt_to_equity"],
            interest_coverage=r["interest_coverage"],
            cash_to_short_debt=r["cash_to_short_debt"],
            ocf_to_debt=r["ocf_to_debt"],
            capex_to_ocf=r["capex_to_ocf"],
            fcf_to_assets=r["fcf_to_assets"],
            net_buyback_pct=r["net_buyback_pct"],
            roic=r["roic"], loss_years=r["loss_years"], fiscal_years=r["fiscal_years"],
            revenue_drop_years=r["revenue_drop_years"], revenue_years=r["revenue_years"],
            earnings_volatility=r["earnings_volatility"],
        )

    def get(self, ticker: str) -> Optional[TickerInfo]:
        r = self.db.conn.execute("SELECT * FROM ticker_quality WHERE ticker = ?", (ticker,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> dict[str, TickerInfo]:
        return {r["ticker"]: self._row(r) for r in self.db.conn.execute("SELECT * FROM ticker_quality")}

    def purge_except(self, keep: Iterable[str]) -> int:
        """Borra los datos de tickers que ya no están en el Universo ni en la watchlist."""
        return _delete_not_in(self.db, "ticker_quality", "ticker", keep)


class BarRepo:
    """Cierres diarios de los subyacentes (para la tendencia).

    `all_closes` y `last_days` salen de una copia en memoria de la tabla (~1,3 millones de cierres con 1.400 días de histórico: leerla de SQLite
    cuesta 3-4 s y se pedía en cada cambio de perfil del Scanner). Se carga la primera vez y cada escritura de este repositorio la mantiene al
    día; borrar o podar la descarta. Solo este repositorio escribe en `daily_bars`."""
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: Optional[dict[str, list[tuple[date, float]]]] = None
        self._provisional: set[tuple[str, date]] = set()      # cierres del día en curso que aún no son el oficial

    def _loaded(self) -> dict[str, list[tuple[date, float]]]:
        if self._cache is None:
            days: dict[str, date] = {}                          # ~1.400 fechas distintas para 1,3 millones de filas
            cache: dict[str, list[tuple[date, float]]] = {}
            provisional: set[tuple[str, date]] = set()
            for ticker, day, close, prov in self.db.conn.execute("SELECT ticker, day, close, provisional FROM daily_bars ORDER BY ticker, day"):
                d = days.get(day)
                if d is None:
                    d = days[day] = date.fromisoformat(day)
                cache.setdefault(ticker, []).append((d, close))
                if prov:
                    provisional.add((ticker, d))
            self._cache, self._provisional = cache, provisional
        return self._cache

    def _put(self, ticker: str, day: date, close: float, provisional: bool) -> None:
        """Refleja en la copia en memoria un cierre escrito (si ya está cargada)."""
        if self._cache is None:
            return
        bars = self._cache.setdefault(ticker, [])
        entry = (day, close)
        if not bars or bars[-1][0] < day:
            bars.append(entry)
        else:
            i = bisect.bisect_left(bars, day, key=lambda b: b[0])
            if i < len(bars) and bars[i][0] == day:
                bars[i] = entry
            else:
                bars.insert(i, entry)
        (self._provisional.add if provisional else self._provisional.discard)((ticker, day))

    def _drop_cache(self) -> None:
        self._cache, self._provisional = None, set()

    def last_days(self, tickers: Iterable[str]) -> dict[str, date]:
        """Último día con cierre OFICIAL guardado de cada ticker (los que no tienen histórico no aparecen).
        Los cierres provisionales del día en curso no cuentan: hay que seguir pidiendo el oficial."""
        cache = self._loaded()
        out: dict[str, date] = {}
        for t in tickers:
            for day, _ in reversed(cache.get(t, ())):
                if (t, day) not in self._provisional:
                    out[t] = day
                    break
        return out

    def all_closes(self, tickers: Iterable[str]) -> dict[str, list[tuple[date, float]]]:
        """Cierres de varios tickers a la vez, en orden ascendente (para el scanner). Listas nuevas: quien llama puede modificarlas."""
        cache = self._loaded()
        return {t: list(cache[t]) for t in set(tickers) if t in cache}

    def closes(self, ticker: str, official_only: bool = False) -> dict[date, float]:
        rows = self.db.conn.execute(
            "SELECT day, close FROM daily_bars WHERE ticker = ?" + (" AND provisional = 0" if official_only else ""),
            (ticker,),
        ).fetchall()
        return {date.fromisoformat(r["day"]): r["close"] for r in rows}

    def upsert(self, ticker: str, bars: Iterable[tuple[date, float]]) -> None:
        bars = list(bars)
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO daily_bars (ticker, day, close, provisional) VALUES (?,?,?,0) "
                "ON CONFLICT(ticker, day) DO UPDATE SET close = excluded.close, provisional = 0",
                [(ticker, d.isoformat(), px) for d, px in bars],
            )
        for d, px in bars:
            self._put(ticker, d, px, False)

    def upsert_ohlc(self, ticker: str, bars: Iterable[tuple]) -> None:
        """Guarda velas (día, cierre, máximo, mínimo). Un máximo o mínimo que no llega no borra el ya guardado."""
        bars = list(bars)
        with self.db.conn:
            self.db.conn.executemany(
                "INSERT INTO daily_bars (ticker, day, close, provisional, high, low) VALUES (?,?,?,0,?,?) "
                "ON CONFLICT(ticker, day) DO UPDATE SET close = excluded.close, provisional = 0, "
                "high = COALESCE(excluded.high, daily_bars.high), low = COALESCE(excluded.low, daily_bars.low)",
                [(ticker, d.isoformat(), px, high, low) for d, px, high, low in bars],
            )
        for d, px, _, _ in bars:
            self._put(ticker, d, px, False)

    def missing_ranges(self, tickers: Iterable[str], recent: int, needed: int) -> list[str]:
        """Tickers con histórico oficial de los que, entre las últimas `recent` sesiones, menos de `needed` tienen máximo y
        mínimo (histórico anterior a la migración v20). Un par de días sueltos sin dato no cuenta."""
        out = []
        for t in tickers:
            rows = self.db.conn.execute(
                "SELECT high, low FROM daily_bars WHERE ticker = ? AND provisional = 0 ORDER BY day DESC LIMIT ?", (t, recent)).fetchall()
            if rows and sum(1 for r in rows if r["high"] is not None and r["low"] is not None) < min(needed, len(rows)):
                out.append(t)
        return out

    def recent_ohlc(self, tickers: Iterable[str], limit: int) -> dict[str, list[tuple[float, float, float]]]:
        """(cierre, máximo, mínimo) de las últimas `limit` sesiones oficiales con máximo y mínimo, en orden ascendente."""
        out: dict[str, list[tuple[float, float, float]]] = {}
        for t in tickers:
            rows = self.db.conn.execute(
                "SELECT close, high, low FROM daily_bars WHERE ticker = ? AND provisional = 0 AND high IS NOT NULL AND low IS NOT NULL "
                "ORDER BY day DESC LIMIT ?", (t, limit)).fetchall()
            if rows:
                out[t] = [(r["close"], r["high"], r["low"]) for r in reversed(rows)]
        return out

    def atr(self, tickers: Iterable[str], period: int) -> dict[str, float]:
        """ATR (Wilder) de cada ticker con las últimas sesiones que tienen máximo y mínimo; sin datos suficientes no aparece."""
        from scanner_opciones.metrics.technical import atr as atr_fn
        out = {}
        for t, bars in self.recent_ohlc(tickers, period * 4).items():
            if (value := atr_fn(bars, period)) is not None:
                out[t] = value
        return out

    def hv(self, tickers: Iterable[str], period: int = 30) -> dict[str, float]:
        """Volatilidad histórica anualizada en % (desviación típica de los rendimientos logarítmicos diarios de las últimas
        `period` sesiones oficiales × √252). Aproxima la HV30 de tastytrade, que no la da para algunos valores; sin
        `period` + 1 cierres no aparece."""
        import math
        import statistics
        out: dict[str, float] = {}
        for t in tickers:
            rows = self.db.conn.execute(
                "SELECT close FROM daily_bars WHERE ticker = ? AND provisional = 0 ORDER BY day DESC LIMIT ?", (t, period + 1)).fetchall()
            closes = [r["close"] for r in reversed(rows)]
            if len(closes) < period + 1 or any(c <= 0 for c in closes):
                continue
            out[t] = statistics.stdev(math.log(b / a) for a, b in zip(closes, closes[1:])) * math.sqrt(252) * 100
        return out

    def upsert_provisional(self, ticker: str, day: date, price: float) -> None:
        """Guarda el último precio visto como cierre provisional de `day`. Nunca pisa un cierre oficial."""
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO daily_bars (ticker, day, close, provisional) VALUES (?,?,?,1) "
                "ON CONFLICT(ticker, day) DO UPDATE SET close = excluded.close WHERE daily_bars.provisional = 1",
                (ticker, day.isoformat(), price),
            )
        if self._cache is not None:
            bars = self._cache.get(ticker, ())
            i = bisect.bisect_left(bars, day, key=lambda b: b[0])
            if i < len(bars) and bars[i][0] == day and (ticker, day) not in self._provisional:
                return      # ya hay un cierre oficial de ese día: no se pisa (igual que en la base)
            self._put(ticker, day, price, True)

    def delete(self, ticker: str) -> None:
        with self.db.conn:
            self.db.conn.execute("DELETE FROM daily_bars WHERE ticker = ?", (ticker,))
        self._drop_cache()

    def prune(self, before: date) -> int:
        """Borra los cierres anteriores a `before` (el histórico no crece sin límite)."""
        with self.db.conn:
            n = self.db.conn.execute("DELETE FROM daily_bars WHERE day < ?", (before.isoformat(),)).rowcount
        self._drop_cache()
        return n

    def purge_except(self, keep: Iterable[str]) -> int:
        n = _delete_not_in(self.db, "daily_bars", "ticker", keep)
        self._drop_cache()
        return n


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

    _UPSERT = (
        "INSERT OR REPLACE INTO snapshots (contract_id, updated_at, bid, ask, last, delta, iv, "
        "open_interest, spread_pct, yield_pct, yield_annualized_pct, iv_rank, iv_percentile, "
        "initial_margin, bid_size, margin_at, bid_size_tws_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )

    @staticmethod
    def _row(contract_id: int, snap: ContractSnapshot) -> tuple:
        return (
            contract_id, snap.updated_at.isoformat(), snap.bid, snap.ask, snap.last, snap.delta,
            snap.iv, snap.open_interest, snap.spread_pct, snap.yield_pct,
            snap.yield_annualized_pct, snap.iv_rank, snap.iv_percentile, snap.initial_margin, snap.bid_size,
            snap.margin_at.isoformat() if snap.margin_at else None,
            snap.bid_size_tws_at.isoformat() if snap.bid_size_tws_at else None,
        )

    def upsert(self, snap: ContractSnapshot) -> bool:
        """Guarda el snapshot. False si el contrato no existe en `contracts`."""
        return self.upsert_many([snap]) == 1

    def upsert_many(self, snaps: Sequence[ContractSnapshot]) -> int:
        """Guarda varios snapshots en UNA transacción (un commit por snapshot era el cuello de botella del
        refresco: ~3 ms cada uno). Los ids de contrato se resuelven de una vez por ticker. Devuelve cuántos se
        guardaron: los contratos que no existen en `contracts` se descartan."""
        if not snaps:
            return 0
        tickers = sorted({s.contract.ticker for s in snaps})
        ids: dict[tuple, int] = {}
        for i in range(0, len(tickers), 500):      # SQLite limita las variables de una consulta
            chunk = tickers[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.db.conn.execute(
                f"SELECT id, ticker, expiry, strike, right FROM contracts WHERE ticker IN ({marks})", chunk
            ):
                ids[(r["ticker"], r["expiry"], r["strike"], r["right"])] = r["id"]
        rows = []
        for snap in snaps:
            c = snap.contract
            cid = ids.get((c.ticker, c.expiry.isoformat(), c.strike, c.right.value))
            if cid is not None:
                rows.append(self._row(cid, snap))
        if rows:
            with self.db.conn:
                self.db.conn.executemany(self._UPSERT, rows)
        return len(rows)

    def all(self, ticker: Optional[str] = None) -> list[ContractSnapshot]:
        # columnas explícitas y filas como tuplas: con ~65.000 contratos, leer por nombre y reconstruir fechas y enums por fila costaba ~1,5 s
        sql = (
            "SELECT c.ticker, c.expiry, c.strike, c.right, c.multiplier, c.con_id, s.updated_at, s.bid, s.ask, s.last, s.delta, s.iv, "
            "s.open_interest, s.spread_pct, s.yield_pct, s.yield_annualized_pct, s.iv_rank, s.iv_percentile, s.initial_margin, s.bid_size, "
            "s.margin_at, s.bid_size_tws_at FROM snapshots s JOIN contracts c ON c.id = s.contract_id"
        )
        params: tuple = ()
        if ticker:
            sql += " WHERE c.ticker = ?"
            params = (ticker,)
        days: dict[str, date] = {}          # unos pocos vencimientos distintos
        rights: dict[str, OptionRight] = {}
        out: list[ContractSnapshot] = []
        for (tk, expiry, strike, right, mult, con_id, updated, bid, ask, last, delta, iv, oi, sp, yp, yap, ivr, ivp, margin, bsize, margin_at,
             tws_at) in self.db.conn.execute(sql + " ORDER BY c.ticker, c.expiry, c.strike", params):
            if (d := days.get(expiry)) is None:
                d = days[expiry] = date.fromisoformat(expiry)
            if (rt := rights.get(right)) is None:
                rt = rights[right] = OptionRight(right)
            out.append(ContractSnapshot(
                contract=OptionContract(ticker=tk, expiry=d, strike=strike, right=rt, multiplier=mult, con_id=con_id),
                updated_at=datetime.fromisoformat(updated), bid=bid, ask=ask, last=last, delta=delta, iv=iv, open_interest=oi, spread_pct=sp,
                yield_pct=yp, yield_annualized_pct=yap, iv_rank=ivr, iv_percentile=ivp, initial_margin=margin, bid_size=bsize,
                margin_at=_dt(margin_at), bid_size_tws_at=_dt(tws_at),
            ))
        return out


class ChainCacheRepo:
    """Lista de puts que existen de cada ticker (`{(vencimiento, strike): símbolo}`), comprimida, con el instante en que se pidió al
    proveedor. Sirve para no repetir la petición tras reiniciar la app con el mercado cerrado. No altera lo que escanea el Scanner."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, ticker: str) -> Optional[tuple[datetime, dict[tuple[date, float], str]]]:
        row = self.db.conn.execute("SELECT fetched_at, data FROM chain_cache WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            return None
        try:
            items = json.loads(zlib.decompress(row["data"]))
            return datetime.fromisoformat(row["fetched_at"]), {(date.fromisoformat(e), float(k)): sym for e, k, sym in items}
        except (zlib.error, ValueError, TypeError):
            return None   # dato dañado: se vuelve a pedir

    def put(self, ticker: str, when: datetime, listing: dict[tuple[date, float], str]) -> None:
        blob = zlib.compress(json.dumps([[e.isoformat(), k, sym] for (e, k), sym in listing.items()], separators=(",", ":")).encode())
        before = self.db.conn.total_changes
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO chain_cache (ticker, fetched_at, data) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET fetched_at = excluded.fetched_at, data = excluded.data",
                (ticker, when.isoformat(), blob),
            )
        self.db.quiet_changes += self.db.conn.total_changes - before
