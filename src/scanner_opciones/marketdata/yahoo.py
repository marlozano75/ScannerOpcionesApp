"""Cambios de valoración de analistas desde Yahoo Finance (librería `yfinance`: gratuita, sin clave y no oficial, así que puede
romperse o limitar las peticiones; en ese caso se conserva lo guardado). Único módulo que importa `yfinance`."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from collections.abc import Mapping
from typing import Callable, Optional

from scanner_opciones.marketdata.analysts import AnalystChange, AnalystError, classify_changes
from scanner_opciones.marketdata.company import CompanyNotesError
from scanner_opciones.domain.errors import CandleError, FinancialsError
from scanner_opciones.marketdata.candles import OhlcBars
from scanner_opciones.marketdata.financials import Financials
from scanner_opciones.marketdata.fx import FxError, FxProvider, RateBook
from scanner_opciones.marketdata.history import EarningsQuarter, HistoryError, last_quarters
from scanner_opciones.marketdata.yahoo_financials import convert, financials_from_statements, statements_currency
from scanner_opciones.marketdata.sectors import etf_sector, yahoo_to_sector

log = logging.getLogger(__name__)

MAX_FAILURES = 8        # fallos seguidos que cortan la pasada (Yahoo limita las peticiones)


def yahoo_symbol(ticker: str) -> str:
    """BRK.B / BRK B -> BRK-B (Yahoo escribe la clase de acciones con guion)."""
    return ticker.replace(".", "-").replace(" ", "-").upper()


def rows_from_frame(frame) -> list[dict]:
    """Filas normalizadas del DataFrame `upgrades_downgrades` de yfinance (índice = fecha del cambio)."""
    if frame is None or len(frame) == 0:
        return []
    out = []
    for stamp, r in frame.iterrows():
        day = stamp.date() if hasattr(stamp, "date") else stamp
        out.append({"day": day, "firm": r.get("Firm"), "action": r.get("Action"), "from": r.get("FromGrade"), "to": r.get("ToGrade"),
                    "pt_from": r.get("priorPriceTarget"), "pt_to": r.get("currentPriceTarget")})
    return out


class YahooAnalysts:
    """Implementa `AnalystProvider`. Una petición por ticker (~0,15 s), en hilos porque `yfinance` es síncrono."""

    def __init__(self, concurrency: int = 4, today: Callable[[], date] = lambda: datetime.now().date()) -> None:
        self.concurrency = concurrency
        self.today = today

    async def get_analyst_changes(self, tickers: list[str], window_days: int) -> dict[str, list[AnalystChange]]:
        try:
            import yfinance
        except ImportError as exc:
            raise AnalystError("Falta la librería yfinance (pip install yfinance)") from exc
        # yfinance escribe en su log como ERROR cada «404: sin datos» (ETF, índices, valores retirados: no tienen analistas) y no es un fallo
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, list[AnalystChange]] = {}
        failures = 0

        def fetch(symbol: str):
            return yfinance.Ticker(symbol).upgrades_downgrades

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    frame = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:        # Yahoo cambia a menudo: el fallo de un ticker no tumba la pasada
                    failures += 1
                    log.debug("Yahoo: sin cambios de analistas de %s (%s)", ticker, exc)
                    return
            failures = 0
            out[ticker] = classify_changes(rows_from_frame(frame), self.today(), window_days)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise AnalystError("Yahoo Finance no responde o limita las peticiones")
        return out


def earnings_from_frame(frame, quarters: int) -> list[EarningsQuarter]:
    """Últimos `quarters` trimestres con BPA real del DataFrame `earnings_dates` de yfinance (índice = fecha y hora; columnas «EPS Estimate» y
    «Reported EPS»; incluye la próxima presentación con el real vacío)."""
    if frame is None or len(frame) == 0:
        return []

    def num(value) -> Optional[float]:
        return None if value is None or value != value else float(value)       # NaN != NaN

    rows = []
    for stamp, r in frame.iterrows():
        actual = num(r.get("Reported EPS"))
        if actual is not None:
            rows.append(EarningsQuarter(stamp.date() if hasattr(stamp, "date") else stamp, actual, num(r.get("EPS Estimate"))))
    return last_quarters(rows, quarters)


class YahooEarningsHistory:
    """Implementa `EarningsHistoryProvider` con `Ticker.earnings_dates` de Yahoo. Una petición por ticker (~0,3 s), en hilos."""

    def __init__(self, concurrency: int = 4) -> None:
        self.concurrency = concurrency

    async def get_earnings_history(self, tickers: list[str], quarters: int) -> dict[str, list[EarningsQuarter]]:
        try:
            import yfinance
        except ImportError as exc:
            raise HistoryError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, list[EarningsQuarter]] = {}
        failures = 0

        def fetch(symbol: str):
            return yfinance.Ticker(symbol).get_earnings_dates(limit=max(12, quarters * 2))

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    frame = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:        # los ETF no tienen resultados; Yahoo cambia a menudo: no tumba la pasada
                    failures += 1
                    log.debug("Yahoo: sin resultados de %s (%s)", ticker, exc)
                    return
            failures = 0
            out[ticker] = earnings_from_frame(frame, quarters)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise HistoryError("Yahoo Finance no responde o limita las peticiones")
        return out


class YahooFx:
    """Implementa `FxProvider` con las cotizaciones `<MONEDA>USD=X` de Yahoo (cierre diario, ~6 años)."""

    def __init__(self, period: str = "8y") -> None:
        self.period = period

    async def get_history(self, currency: str) -> dict[date, float]:
        try:
            import yfinance
        except ImportError as exc:
            raise FxError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        def fetch():
            return yfinance.Ticker(f"{currency}USD=X").history(period=self.period, interval="1d")["Close"]

        try:
            closes = await asyncio.to_thread(fetch)
        except Exception as exc:
            raise FxError(f"Yahoo: cambio {currency}/USD no disponible ({type(exc).__name__}: {str(exc)[:120]})") from exc
        out = {stamp.date(): float(v) for stamp, v in closes.items() if v == v and v > 0}
        if not out:
            raise FxError(f"Yahoo no devolvió cotizaciones de {currency}/USD")
        return out


class YahooFinancials:
    """Implementa `FinancialsProvider` con los estados financieros de Yahoo (trimestrales y anuales: balance, flujo de caja y resultados), pensado
    como respaldo de SEC EDGAR. Siete peticiones por ticker, en hilos; las cuentas llegan en la moneda de la empresa y los importes se pasan a
    dólares con `fx`. Un ticker sin estados (ETF) o que falla no aparece."""

    def __init__(self, fx: Optional[FxProvider] = None, concurrency: int = 4, today: Callable[[], date] = date.today) -> None:
        self.concurrency = concurrency
        self._rates = RateBook(fx)
        self._today = today

    async def get_financials(self, tickers: list[str]) -> dict[str, Financials]:
        try:
            import yfinance
        except ImportError as exc:
            raise FinancialsError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, Financials] = {}
        failures = 0

        def fetch(symbol: str):
            t = yfinance.Ticker(symbol)
            balance_q = t.quarterly_balance_sheet
            if balance_q is None or balance_q.empty:        # ETF o valor sin estados: no se pide el resto
                return None
            stmts = {"balance_q": balance_q, "balance_a": t.balance_sheet, "cash_q": t.quarterly_cashflow, "cash_a": t.cashflow,
                     "income_q": t.quarterly_income_stmt, "income_a": t.income_stmt}
            return stmts, (t.get_info() or {})

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    got = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:
                    failures += 1
                    log.debug("Yahoo: sin estados financieros de %s (%s)", ticker, exc)
                    return
            failures = 0
            if got is None:
                return
            stmts, info = got
            fin = financials_from_statements(stmts, self._today())
            if fin is None:
                return
            declared = info.get("financialCurrency")
            rate_at = await self._rates.prepare({declared}) if declared and declared != "USD" else None
            rate = rate_at(declared, self._today()) if rate_at is not None else None
            out[ticker] = convert(fin, statements_currency(stmts, info, declared, rate, self._today()), rate_at)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise FinancialsError("Yahoo Finance no responde o limita las peticiones")
        return out


class YahooSectors:
    """Implementa `SectorProvider` con `Ticker.info` de Yahoo (sector e industria), traducido a los nombres de IBKR. Una petición por ticker (~0,5 s)."""

    def __init__(self, concurrency: int = 4) -> None:
        self.concurrency = concurrency

    async def get_sectors(self, tickers) -> dict[str, tuple[str, Optional[str]]]:
        try:
            import yfinance
        except ImportError as exc:
            raise AnalystError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, tuple[str, Optional[str]]] = {}
        failures = 0

        def fetch(symbol: str) -> dict:
            ticker = yfinance.Ticker(symbol)
            info = ticker.info or {}
            if not info.get("sector") and str(info.get("quoteType") or "").upper() in ("ETF", "MUTUALFUND"):
                try:        # los ETF no traen sector: se deduce de los pesos de su cartera
                    info = {**info, "_weights": ticker.funds_data.sector_weightings}
                except Exception as exc:
                    log.debug("Yahoo: sin pesos por sector de %s (%s)", symbol, exc)
                    info = {**info, "_weights": None}
            return info

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    info = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:
                    failures += 1
                    log.debug("Yahoo: sin sector de %s (%s)", ticker, exc)
                    return
            failures = 0
            sector = yahoo_to_sector(info.get("sector"))
            if not sector and "_weights" in info:
                sector, category = etf_sector(info["_weights"], info.get("category"))
                if sector:
                    out[ticker] = (sector, category)
                return
            if sector:
                out[ticker] = (sector, info.get("industry") or None)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise AnalystError("Yahoo Finance no responde o limita las peticiones")
        return out


class YahooDescriptions:
    """Implementa `DescriptionProvider` con `Ticker.info['longBusinessSummary']` de Yahoo (en inglés). Una petición por ticker (~0,5 s)."""

    def __init__(self, concurrency: int = 4) -> None:
        self.concurrency = concurrency

    async def get_descriptions(self, tickers: list[str]) -> dict[str, str]:
        try:
            import yfinance
        except ImportError as exc:
            raise CompanyNotesError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, str] = {}
        failures = 0

        def fetch(symbol: str) -> str:
            return str((yfinance.Ticker(symbol).info or {}).get("longBusinessSummary") or "")

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    out[ticker] = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                    failures = 0
                except Exception as exc:
                    failures += 1
                    log.debug("Yahoo: sin descripción de %s (%s)", ticker, exc)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise CompanyNotesError("Yahoo Finance no responde o limita las peticiones")
        return out


def ohlc_from_frame(frame) -> OhlcBars:
    """Velas diarias (día, cierre, máximo, mínimo) del DataFrame de `Ticker.history` (índice = fecha de la sesión, en la zona de la bolsa).
    Se saltan las filas sin cierre; un máximo o mínimo que falta queda en None."""
    if frame is None or len(frame) == 0:
        return []
    out: OhlcBars = []
    for stamp, r in frame.iterrows():
        close = r.get("Close")
        if close is None or close != close or close <= 0:
            continue
        hi, lo = r.get("High"), r.get("Low")
        out.append((stamp.date() if hasattr(stamp, "date") else stamp, float(close),
                    float(hi) if hi is not None and hi == hi else None, float(lo) if lo is not None and lo == lo else None))
    return sorted(out)


class InstrumentYahooSymbols(Mapping):
    """Vista en vivo de `Settings.instruments`: ticker de la app -> símbolo de Yahoo, solo de los instrumentos que lo tienen."""

    def __init__(self, instruments) -> None:
        self._instruments = instruments

    def __getitem__(self, ticker: str) -> str:
        symbol = getattr(self._instruments.get(ticker), "yahoo_symbol", None)
        if not symbol:
            raise KeyError(ticker)
        return symbol

    def __iter__(self):
        return (t for t, i in list(self._instruments.items()) if i.yahoo_symbol)

    def __len__(self) -> int:
        return sum(1 for i in list(self._instruments.values()) if i.yahoo_symbol)


class YahooCandles:
    """Implementa `CandleProvider` para los instrumentos que tastytrade no cubre (índices como el Euro Stoxx 50). `symbols` traduce el ticker de la app al de Yahoo
    (`^STOXX50E`). Los cierres no se ajustan (un índice no tiene splits ni dividendos). Una petición por ticker, en hilos porque `yfinance` es síncrono."""

    def __init__(self, symbols: dict[str, str], concurrency: int = 4, today: Callable[[], date] = lambda: datetime.now().date()) -> None:
        self.symbols = symbols      # se conserva la referencia: los valores europeos dados de alta desde el Universo aparecen sin reiniciar
        self.concurrency = concurrency
        self.today = today

    async def get_daily_ohlc(self, tickers: list[str], days: int) -> dict[str, OhlcBars]:
        try:
            import yfinance
        except ImportError as exc:
            raise CandleError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        start = self.today() - timedelta(days=days)
        end = self.today() + timedelta(days=1)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, OhlcBars] = {}
        failed: list[str] = []

        def fetch(symbol: str):
            return yfinance.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=False)

        async def one(ticker: str) -> None:
            symbol = self.symbols.get(ticker)
            if symbol is None:
                return
            async with sem:
                try:
                    bars = ohlc_from_frame(await asyncio.to_thread(fetch, symbol))
                except Exception as exc:
                    log.warning("Yahoo: sin velas de %s (%s)", ticker, exc)
                    failed.append(ticker)
                    return
            if bars:
                out[ticker] = bars

        await asyncio.gather(*(one(t) for t in tickers))
        if failed and not out:
            raise CandleError("Yahoo Finance no devolvió velas de " + ", ".join(failed))
        return out

    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, list[tuple[date, float]]]:
        return {t: [(d, px) for d, px, *_ in bars] for t, bars in (await self.get_daily_ohlc(tickers, days)).items()}
