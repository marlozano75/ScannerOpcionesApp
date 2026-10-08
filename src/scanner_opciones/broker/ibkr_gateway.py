"""Adaptador real de IBKR (TWS / IB Gateway) sobre ib_async.

SOLO LECTURA: este módulo nunca llama a placeOrder; el margen se obtiene con órdenes what-if.
No se puede probar sin TWS: ver tests/manual/test_ibkr_smoke.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from ib_async import IB, Future, Index, MarketOrder, Option, Stock

from scanner_opciones.broker import ibkr_mapper as m
from scanner_opciones.broker.rate_limiter import AsyncRateLimiter
from scanner_opciones.config.settings import IbkrSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.errors import (
    BrokerDisconnectedError, BrokerError, DataUnavailableError, UnsupportedTickerError,
)
from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, UnderlyingQuote, VixData,
)

log = logging.getLogger(__name__)


class _DropMessages(logging.Filter):
    """Descarta líneas de log de ib_async cuyo mensaje empieza por alguno de `prefixes`."""

    def __init__(self, *prefixes: str) -> None:
        super().__init__()
        self.prefixes = prefixes
        self.depth = 0  # varios usos a la vez: el filtro se quita al salir el último

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(self.prefixes)


# avisos ESPERADOS al validar combinaciones que no existen: «Error 200 ... No se encuentra definición»
# (wrapper) y «Unknown contract» (ib)
_UNKNOWN_FILTER = _DropMessages("Error 200,", "Unknown contract")
# 10197: se resume en un único aviso por lote de cotizaciones (ver `IBKRGateway.get_quotes`)
_COMPETING_FILTER = _DropMessages("Error 10197,")
COMPETING_SESSION = 10197


@contextmanager
def _quiet(flt: _DropMessages):
    loggers = [logging.getLogger("ib_async.wrapper"), logging.getLogger("ib_async.ib")]
    if flt.depth == 0:
        for lg in loggers:
            lg.addFilter(flt)
    flt.depth += 1
    try:
        yield
    finally:
        flt.depth -= 1
        if flt.depth == 0:
            for lg in loggers:
                lg.removeFilter(flt)


def _quiet_unknown_contracts():
    """Mientras se valida una lista de contratos, los inexistentes no llenan el log: la lista
    devuelta ya dice cuáles existen y quien llama registra un único resumen."""
    return _quiet(_UNKNOWN_FILTER)


class IBKRGateway:
    def __init__(self, settings: IbkrSettings, now=datetime.now) -> None:
        self.s = settings
        self.ib = IB()
        self.now = now
        self._stocks: dict[str, Stock] = {}
        self._sector_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
        self._vix_futures_cache: Optional[tuple[date, list]] = None
        self._summary_request: Optional[asyncio.Future] = None   # petición de account summary en vuelo (una como máximo)
        self._historical_counts: Counter[str] = Counter()
        self._historical_limiter = AsyncRateLimiter(
            settings.historical_requests_per_10min, 600, on_wait=self._log_pacing
        )

    # ---- conexión -------------------------------------------------------------------------
    async def connect(self) -> None:
        try:
            await self.ib.connectAsync(
                self.s.host, self.s.port, clientId=self.s.client_id, timeout=self.s.connect_timeout_seconds
            )
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as exc:
            raise BrokerDisconnectedError(
                f"No se pudo conectar a TWS en {self.s.host}:{self.s.port} ({self.s.mode.value}): {exc!r}"
            ) from exc
        self.ib.reqMarketDataType(self.s.market_data_type)

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def _log_pacing(self, delay: float) -> None:
        log.info(
            "Límite de peticiones históricas de IBKR alcanzado: se espera ~%.0f s (peticiones desde el arranque: %s)",
            delay, ", ".join(f"{k} {v}" for k, v in sorted(self._historical_counts.items())) or "ninguna",
        )

    def historical_request_counts(self) -> dict[str, int]:
        return dict(self._historical_counts)

    def pacing_wait_seconds(self) -> float:
        return self._historical_limiter.wait_remaining()

    def _require(self) -> None:
        if not self.ib.isConnected():
            raise BrokerDisconnectedError("Sin conexión con TWS")

    # ---- cuenta ---------------------------------------------------------------------------
    def _account_id(self) -> str:
        accounts = self.ib.managedAccounts()
        if not accounts:
            raise DataUnavailableError("TWS no devolvió ninguna cuenta gestionada")
        if self.s.account:
            if self.s.account not in accounts:
                raise DataUnavailableError(
                    f"La cuenta {self.s.account} (ibkr.accounts) no está entre las gestionadas: {', '.join(accounts)}"
                )
            return self.s.account
        if len(accounts) > 1:
            log.warning("TWS gestiona varias cuentas (%s) y ibkr.accounts no está fijado: se usa %s",
                        ", ".join(accounts), accounts[0])
        return accounts[0]

    async def get_account_summary(self) -> AccountSummary:
        self._require()
        account = self._account_id()
        # accountValues() se mantiene actualizado por la suscripción automática de ib_async
        values = list(self.ib.accountValues(account))
        # accountSummary aporta valores que accountValues no trae (p. ej. HighestSeverity)
        values += await self._summary_values(account)
        return m.build_account_summary(values, self.s.account_tags, account, self.now())

    async def _summary_values(self, account: str) -> list:
        """Valores de `accountSummary`. `ib_async.accountSummaryAsync` abre una suscripción NUEVA en cada llamada
        mientras el resumen no está cargado y nunca la cierra: con TWS sin responder (error 1100) y un refresco
        cada minuto se acumulaban hasta que TWS rechazaba con el error 322 («máximo de peticiones de account
        summary»). Aquí hay como mucho una petición en vuelo, y quien llama puede agotar su tiempo sin cancelarla."""
        wrapper = getattr(self.ib, "wrapper", None)
        if wrapper is None:                       # dobles de prueba sin wrapper
            return list(await self.ib.accountSummaryAsync(account))
        if not wrapper.acctSummary:
            if self._summary_request is None or self._summary_request.done():
                self._summary_request = asyncio.ensure_future(self.ib.reqAccountSummaryAsync())
            await asyncio.shield(self._summary_request)
        return [v for v in wrapper.acctSummary.values() if not account or v.account == account]

    async def get_positions(self) -> list[Position]:
        self._require()
        out: list[Position] = []
        for item in self.ib.portfolio(self._account_id()):
            c = item.contract
            if c.secType == "STK":
                sector, _ = await self.get_sector_info(c.symbol)
                out.append(Position(c.symbol, item.position, float(item.marketValue), sector))
            elif c.secType == "OPT":
                sector, _ = await self.get_sector_info(c.symbol)
                oc = OptionContract(
                    c.symbol, m.parse_expiry(c.lastTradeDateOrContractMonth), float(c.strike),
                    OptionRight(c.right[0]), int(c.multiplier or 100), c.conId,
                )
                out.append(Position(c.symbol, item.position, float(item.marketValue), sector, oc))
        return out

    # ---- utilidades con timeout -----------------------------------------------------------
    HISTORICAL_TIMEOUT = 30.0

    async def _historical(
        self, contract, duration: str, what: str, bar: str = "1 day", use_rth: bool = True, kind: str = "otro"
    ) -> list:
        """reqHistoricalData con pacing y timeout. Lanza DataUnavailableError si no responde.
        `kind` solo sirve para contar las peticiones por tipo (log y diagnóstico del límite de IBKR)."""
        await self._historical_limiter.acquire()
        self._historical_counts[kind] += 1
        try:
            bars = await asyncio.wait_for(
                self.ib.reqHistoricalDataAsync(
                    contract, endDateTime="", durationStr=duration, barSizeSetting=bar,
                    whatToShow=what, useRTH=use_rth, formatDate=1,
                ),
                timeout=self.HISTORICAL_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise DataUnavailableError(f"Timeout pidiendo histórico de {contract.symbol}") from exc
        return list(bars or [])

    @staticmethod
    def _bar_day(bar) -> date:
        d = bar.date
        return d.date() if isinstance(d, datetime) else d

    async def _live_price(self, contract) -> Optional[float]:
        """Precio por streaming con espera acotada (nunca se cuelga). None si no hay datos."""
        tick = self.ib.reqMktData(contract, "", False, False)
        try:
            waited, step = 0.0, 0.25
            while waited < self.s.quote_wait_seconds:
                price = m.num(tick.marketPrice())
                if price is not None:
                    return price
                await asyncio.sleep(step)
                waited += step
            return m.num(tick.close)
        finally:
            self.ib.cancelMktData(contract)

    async def _last_close(self, contract, use_rth: bool = True, kind: str = "precio") -> Optional[float]:
        bars = await self._historical(contract, "1 W", "TRADES", use_rth=use_rth, kind=kind)
        return m.num(bars[-1].close) if bars else None

    # ---- subyacente -----------------------------------------------------------------------
    async def _stock(self, ticker: str) -> Stock:
        if ticker in self._stocks:
            return self._stocks[ticker]
        self._require()
        stock = Stock(m.ib_symbol(ticker), "SMART", "USD")   # «BRK.B» -> «BRK B»
        qualified = await self.ib.qualifyContractsAsync(stock)
        if not qualified or not stock.conId:
            raise UnsupportedTickerError(f"Ticker no reconocido por IBKR: {ticker}")
        self._stocks[ticker] = stock
        return stock

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        if ticker in self._sector_cache:
            return self._sector_cache[ticker]
        stock = await self._stock(ticker)
        details = await self.ib.reqContractDetailsAsync(stock)
        if not details:
            raise DataUnavailableError(f"Sin contract details para {ticker}")
        d = details[0]
        result = (d.industry or None, d.category or None)
        self._sector_cache[ticker] = result
        return result

    async def get_underlying_price(self, ticker: str) -> Optional[float]:
        stock = await self._stock(ticker)
        price = await self._live_price(stock)
        if price is None:  # sin suscripción en tiempo real: último cierre
            price = await self._last_close(stock)
        return price

    PRICE_BATCH = 50

    async def get_underlying_quotes(self, tickers: Sequence[str]) -> dict[str, UnderlyingQuote]:
        """Precio e IV (30 días, tick 106) de varios subyacentes con una espera acotada por lote.
        Lo que no llegue (p. ej. sin suscripción) queda como None: se conserva el valor anterior."""
        self._require()
        stocks: dict[str, Stock] = {}
        for t in tickers:
            try:
                stocks[t] = await self._stock(t)
            except DataUnavailableError:
                continue
        out: dict[str, UnderlyingQuote] = {}
        items = list(stocks.items())
        for i in range(0, len(items), self.PRICE_BATCH):
            chunk = items[i : i + self.PRICE_BATCH]
            ticks = {t: self.ib.reqMktData(s, "106", False, False) for t, s in chunk}  # 106 = IV de opciones
            try:
                waited, step = 0.0, 0.25
                while waited < self.s.quote_wait_seconds:
                    if all(
                        m.num(tk.marketPrice()) is not None and m.num(tk.impliedVolatility) is not None
                        for tk in ticks.values()
                    ):
                        break
                    await asyncio.sleep(step)
                    waited += step
            finally:
                for _, s in chunk:
                    self.ib.cancelMktData(s)
            for t, tk in ticks.items():
                q = UnderlyingQuote(price=m.num(tk.marketPrice()), iv=m.num(tk.impliedVolatility))
                if q.price is not None or q.iv is not None:
                    out[t] = q
        return out

    async def get_option_chain(self, ticker: str) -> OptionChain:
        stock = await self._stock(ticker)
        chains = await self.ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
        chain = m.pick_chain(chains, ticker)
        if chain is None:
            raise UnsupportedTickerError(f"Sin cadena de opciones para {ticker}")
        expiries = sorted(m.parse_expiry(e) for e in chain.expirations)
        return OptionChain(ticker, expiries, sorted(float(s) for s in chain.strikes), int(chain.multiplier or 100))

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        stock = await self._stock(ticker)
        tick = self.ib.reqMktData(stock, "456", False, False)  # 456 = IB Dividends
        try:
            await asyncio.sleep(min(2.0, self.s.quote_wait_seconds))
            div = tick.dividends
        finally:
            self.ib.cancelMktData(stock)
        if div is None or div.nextDate is None:
            return None
        return (div.nextDate - self.now().date()).days

    async def get_days_to_ex_dividend_many(self, tickers: Sequence[str]) -> dict[str, Optional[int]]:
        """Una sola espera acotada para todos (los ticks 456 llegan a la vez); termina antes si
        ya han llegado todos."""
        self._require()
        stocks: dict[str, Stock] = {}
        for t in tickers:
            try:
                stocks[t] = await self._stock(t)
            except DataUnavailableError:
                continue
        out: dict[str, Optional[int]] = {}
        items = list(stocks.items())
        for i in range(0, len(items), self.PRICE_BATCH):
            chunk = items[i : i + self.PRICE_BATCH]
            ticks = {t: self.ib.reqMktData(s, "456", False, False) for t, s in chunk}  # 456 = IB Dividends
            try:
                waited, step, limit = 0.0, 0.25, min(2.0, self.s.quote_wait_seconds)
                while waited < limit and not all(tk.dividends is not None for tk in ticks.values()):
                    await asyncio.sleep(step)
                    waited += step
            finally:
                for _, s in chunk:
                    self.ib.cancelMktData(s)
            today = self.now().date()
            for t, tk in ticks.items():
                div = tk.dividends
                out[t] = None if div is None or div.nextDate is None else (div.nextDate - today).days
        return out

    # ---- opciones -------------------------------------------------------------------------
    @staticmethod
    def _ib_option(c: OptionContract) -> Option:
        opt = Option(m.ib_symbol(c.ticker), m.format_expiry(c.expiry), c.strike, c.right.value, "SMART",
                     multiplier=str(c.multiplier), currency="USD")
        if c.con_id:
            opt.conId = c.con_id  # ya validado: no hace falta volver a cualificar
        return opt

    QUALIFY_BATCH = 50

    async def qualify_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        """Valida los contratos contra IBKR UNA vez (en la actualización diaria). Los strikes de la
        cadena no existen para todos los vencimientos: los inexistentes se descartan aquí."""
        self._require()
        out: list[OptionContract] = []
        for i in range(0, len(contracts), self.QUALIFY_BATCH):
            batch = list(contracts[i : i + self.QUALIFY_BATCH])
            opts = [self._ib_option(c) for c in batch]
            todo = [o for o in opts if not o.conId]
            if todo:
                with _quiet_unknown_contracts():
                    await self.ib.qualifyContractsAsync(*todo)
            for c, o in zip(batch, opts):
                if o.conId:
                    out.append(replace(c, con_id=o.conId))
        return out

    @staticmethod
    def _quote_ready(c: OptionContract, t) -> bool:
        oi = t.putOpenInterest if c.right.value == "P" else t.callOpenInterest
        return (
            m.num(t.bid) is not None and m.num(t.ask) is not None
            and t.modelGreeks is not None and m.num(oi) is not None
        )

    async def get_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        self._require()
        pairs = [(c, self._ib_option(c)) for c in contracts]
        todo = [o for _, o in pairs if not o.conId]  # normalmente vacío: se validaron en la actualización diaria
        if todo:
            with _quiet_unknown_contracts():
                await self.ib.qualifyContractsAsync(*todo)
        valid = [(c, o) for c, o in pairs if o.conId]
        competing: dict[str, int] = {}   # ticker -> contratos con el error 10197

        def on_error(req_id, code, message, contract=None) -> None:
            if code == COMPETING_SESSION:
                name = getattr(contract, "symbol", None) or "?"
                competing[name] = competing.get(name, 0) + 1

        self.ib.errorEvent += on_error
        try:
            with _quiet(_COMPETING_FILTER):
                tickers = {c: self.ib.reqMktData(o, "101", False, False) for c, o in valid}  # 101 = open interest
                try:
                    # espera hasta `quote_wait_seconds`, pero sale en cuanto todos tienen sus datos
                    waited, step = 0.0, 0.25
                    while waited < self.s.quote_wait_seconds and not all(
                        self._quote_ready(c, t) for c, t in tickers.items()
                    ):
                        await asyncio.sleep(step)
                        waited += step
                finally:
                    for _, o in valid:
                        self.ib.cancelMktData(o)
        finally:
            self.ib.errorEvent -= on_error
        if competing:
            log.warning(
                "Error 10197 (IBKR cree que otra sesión recibe datos en directo) en %d de %d contratos del lote: %s. "
                "Esos contratos conservan su última cotización. Si no hay otra sesión abierta con este usuario "
                "(TWS, IBKR Mobile, Client Portal, la cuenta real en otro equipo) suele ser pasajero.",
                sum(competing.values()), len(valid),
                ", ".join(f"{t}({n})" for t, n in sorted(competing.items())),
            )
        out: dict[OptionContract, OptionQuote] = {}
        for c, t in tickers.items():
            g = t.modelGreeks
            oi = t.putOpenInterest if c.right.value == "P" else t.callOpenInterest
            oi = m.num(oi)
            bid_size = m.num(t.bidSize)
            out[c] = OptionQuote(
                bid=m.num(t.bid), ask=m.num(t.ask), last=m.num(t.last),
                bid_size=int(bid_size) if bid_size is not None else None,
                delta=m.num(g.delta) if g else None,
                iv=m.num(g.impliedVol) if g else None,
                open_interest=int(oi) if oi is not None else None,
            )
        return out

    async def what_if_margin(self, contract: OptionContract, quantity: int = 1) -> Optional[float]:
        self._require()
        opt = self._ib_option(contract)
        limit = self.s.what_if_timeout_seconds
        try:
            if not opt.conId:  # los contratos guardados ya traen conId: no hace falta cualificar de nuevo
                await asyncio.wait_for(self.ib.qualifyContractsAsync(opt), timeout=limit)
            if not opt.conId:
                return None
            # whatIfOrderAsync fuerza whatIf=True: IBKR calcula el margen SIN enviar la orden.
            order = MarketOrder("SELL", quantity, tif="DAY", account=self._account_id())
            # tif explícito: evita el aviso 10349. Si TWS pierde la conexión con IBKR (error 1100) no responde nunca:
            # sin tiempo máximo el ciclo de refresco quedaba colgado.
            state = await asyncio.wait_for(self.ib.whatIfOrderAsync(opt, order), timeout=limit)
        except asyncio.TimeoutError as exc:
            raise DataUnavailableError(f"TWS no respondió al what-if de {contract.ticker} en {limit:.0f} s") from exc
        return m.parse_margin_change(getattr(state, "initMarginChange", None))

    # ---- VIX ------------------------------------------------------------------------------
    async def get_vix_data(self, history_days: int, futures_ahead: int) -> VixData:
        """VIX y futuros a partir de barras históricas diarias (no exigen suscripción en tiempo real;
        el último dato es el cierre / la última barra del día, válido porque el VIX no necesita
        tiempo real). Los futuros se degradan a lista vacía si fallan."""
        self._require()
        vix = Index("VIX", "CBOE")
        await self.ib.qualifyContractsAsync(vix)
        bars = await self._historical(vix, f"{max(history_days * 2 + 4, 10)} D", "TRADES", kind="vix")
        closes = [(self._bar_day(b), float(b.close)) for b in bars if m.num(b.close) is not None]
        current = closes[-1][1] if closes else None
        closes = closes[-history_days:]

        futures: list[tuple[date, float]] = []
        try:
            today = self.now().date()
            if self._vix_futures_cache is not None and self._vix_futures_cache[0] == today:
                details = self._vix_futures_cache[1]  # la lista de vencimientos no cambia en el día
            else:
                details = await asyncio.wait_for(
                    self.ib.reqContractDetailsAsync(Future("VIX", exchange="CFE", currency="USD")), timeout=20
                )
                self._vix_futures_cache = (today, details)
            by_expiry = {}
            for d in details:
                k = d.contract.lastTradeDateOrContractMonth
                if len(k) == 8:
                    by_expiry[datetime.strptime(k, "%Y%m%d").date()] = d.contract
            upcoming = sorted((e, c) for e, c in by_expiry.items() if e >= today)[:futures_ahead]
            for expiry, contract in upcoming:
                px = await self._last_close(contract, use_rth=False, kind="futuros_vix")  # futuros CFE: sin RTH
                if px is not None:
                    futures.append((expiry, px))
        except (BrokerError, asyncio.TimeoutError) as exc:
            log.warning("Futuros del VIX no disponibles: %r", exc)
        except Exception as exc:  # noqa: BLE001 - sin suscripción a CFE: se degrada, no se cae
            log.warning("No se pudieron obtener los futuros del VIX: %r", exc)
        return VixData(current, closes, futures, self.now())
