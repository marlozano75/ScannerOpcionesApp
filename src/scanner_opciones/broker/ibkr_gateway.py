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
from typing import Mapping, Optional, Sequence

from ib_async import IB, Future, Index, MarketOrder, Option, StartupFetch, Stock
from ib_async.ib import StartupFetchALL, StartupFetchNONE

from scanner_opciones.broker import ibkr_mapper as m
from scanner_opciones.broker.rate_limiter import AsyncRateLimiter
from scanner_opciones.config.settings import IbkrSettings, InstrumentSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.errors import (
    BrokerDisconnectedError, BrokerError, DataUnavailableError, UnsupportedTickerError,
)
from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, UnderlyingQuote, VixData,
)

log = logging.getLogger(__name__)

# TWS solo mantiene UNA suscripción de actualizaciones de cuenta (`reqAccountUpdates`) y `connectAsync` la pide por defecto al conectar. La
# conexión principal la hace explícitamente (`_subscribe_portfolio`), y la de los instrumentos no la necesita: si la pidiera, le quitaría la
# suscripción a la principal y `portfolio()` quedaría vacío (medido 2026-10-29 con IB Gateway: error «no completa la suscripción» cada minuto).
FETCH_WITHOUT_ACCOUNT_UPDATES = StartupFetchALL & ~StartupFetch.ACCOUNT_UPDATES & ~StartupFetch.SUB_ACCOUNT_UPDATES


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
# 10090: «no está suscrito a parte de los datos»; es informativo (los ticks diferidos llegan igual) y salía una vez por contrato
_COMPETING_FILTER = _DropMessages("Error 10197,", "Error 10090,")
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
    def __init__(self, settings: IbkrSettings, now=datetime.now, instruments: Optional[Mapping[str, InstrumentSettings]] = None) -> None:
        self.s = settings
        self.instruments: Mapping[str, InstrumentSettings] = dict(instruments or {})   # índices y otros subyacentes que no son acciones de EE. UU.
        self._ib_aux: Optional[IB] = None     # segunda conexión para los datos de mercado de los instrumentos (ver `_aux_ib`)
        self.ib = IB()
        self.now = now
        self._stocks: dict[str, Stock | Index] = {}
        self._updates_account: Optional[str] = None   # cuenta suscrita con reqAccountUpdates (la de `portfolio()`)
        self._last_option_values: dict[int, float] = {}   # conId → último valor de mercado leído (para avisar de saltos)
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
                self.s.host, self.s.port, clientId=self.s.client_id, timeout=self.s.connect_timeout_seconds,
                fetchFields=FETCH_WITHOUT_ACCOUNT_UPDATES,
            )
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as exc:
            raise BrokerDisconnectedError(
                f"No se pudo conectar a TWS en {self.s.host}:{self.s.port} ({self.s.mode.value}): {exc!r}"
            ) from exc
        self.ib.reqMarketDataType(self.s.market_data_type)
        self._updates_account = None    # la suscripción de la conexión anterior ya no vale

    async def disconnect(self) -> None:
        if self._ib_aux is not None and self._ib_aux.isConnected():
            self._ib_aux.disconnect()
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

    # ---- instrumentos que no son acciones de EE. UU. ----------------------------------------
    async def _aux_ib(self) -> IB:
        """Conexión aparte para los datos de mercado de los instrumentos (índices de Eurex...). Medido contra TWS (2026-10-25): sin suscripción de la bolsa,
        los datos diferidos (tipo 3) traen el open interest y la delta, y los congelados (tipo 2) el último bid/ask pero SIN open interest; y una vez que una
        conexión ha pedido datos congelados, TWS responde congelado también a los pedidos con tipo 3. La conexión principal usa `market_data_type`
        (2), así que los instrumentos necesitan la suya, que solo pide `instruments_market_data_type`."""
        if self._ib_aux is None:
            self._ib_aux = IB()
        if not self._ib_aux.isConnected():
            try:
                await self._ib_aux.connectAsync(self.s.host, self.s.port, clientId=self.s.client_id + self.s.instruments_client_offset,
                                                timeout=self.s.connect_timeout_seconds, fetchFields=StartupFetchNONE)
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as exc:
                raise BrokerDisconnectedError(f"No se pudo abrir la conexión de datos de los instrumentos en {self.s.host}:{self.s.port}: {exc!r}") from exc
            self._ib_aux.reqMarketDataType(self.s.instruments_market_data_type)
        return self._ib_aux

    async def _market_ib(self, ticker: str) -> IB:
        """Conexión con la que se piden los datos de mercado de `ticker`; si la de los instrumentos no se puede abrir, la principal (datos congelados)."""
        if ticker not in self.instruments:
            return self.ib
        try:
            return await self._aux_ib()
        except BrokerDisconnectedError as exc:
            log.warning("%s: se piden los datos de %s por la conexión principal (sin open interest): %s", type(self).__name__, ticker, exc)
            return self.ib

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
        try:
            await self._subscribe_portfolio(account)   # sin ella accountValues() va vacío (y no trae el alias)
        except BrokerError as exc:      # medido 2026-10-29: sin la suscripción llegan igualmente los valores de cuenta (NLV, márgenes, tipo de cambio)
            log.warning("%s; la cuenta se lee sin ella", exc)
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

    async def _subscribe_portfolio(self, account: str) -> None:
        """`ib.portfolio()` solo se llena para la cuenta suscrita con `reqAccountUpdates`, y TWS admite UNA a la vez:
        con varias cuentas gestionadas, `connect` las suscribe todas y no queda ninguna (verificado contra TWS con
        U14977035 y U20571357: 0 posiciones en ambas tras 8 s). Se suscribe explícitamente la cuenta de la app."""
        if self._updates_account == account:
            return
        try:
            await asyncio.wait_for(self.ib.reqAccountUpdatesAsync(account), timeout=self.s.connect_timeout_seconds * 2)
        except asyncio.TimeoutError:
            # medido 2026-10-29 con IB Gateway: la suscripción no termina mientras haya otra sesión abierta con el mismo usuario (otro TWS,
            # la app móvil o un segundo usuario de la cuenta); `portfolio()` queda vacío aunque `positions()` y los valores de cuenta lleguen
            raise BrokerError("IBKR no completa la suscripción a la cuenta y las posiciones: ¿hay otra sesión abierta con el mismo usuario "
                              "(otro TWS, la app móvil o un segundo usuario)?") from None
        self._updates_account = account

    async def get_positions(self) -> list[Position]:
        self._require()
        out: list[Position] = []
        account = self._account_id()
        await self._subscribe_portfolio(account)
        for item in self.ib.portfolio(account):
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
                out.append(Position(c.symbol, item.position, float(item.marketValue), sector, oc, float(item.averageCost)))
                self._log_option_value(oc, item)
        return out

    def _log_option_value(self, oc: OptionContract, item) -> None:
        """Deja en el log lo que TWS entrega de cada opción (valor de mercado, coste medio, precio de mercado) y avisa si el
        valor salta más de un 10 % entre dos lecturas: así se distingue un dato inicial distinto de TWS de un error de cálculo."""
        value = float(item.marketValue)
        log.debug("Posición %s %s %s %s: marketValue=%.2f averageCost=%.4f marketPrice=%s", oc.ticker, oc.expiry, oc.strike,
                  oc.right.value, value, float(item.averageCost), getattr(item, "marketPrice", None))
        before = self._last_option_values.get(oc.con_id)
        self._last_option_values[oc.con_id] = value
        if before and abs(value - before) > 0.10 * abs(before):
            log.info("Posición %s %s %s: el valor de mercado pasó de %.2f a %.2f entre dos lecturas (marketPrice=%s, averageCost=%.4f)",
                     oc.ticker, oc.expiry, oc.strike, before, value, getattr(item, "marketPrice", None), float(item.averageCost))

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

    async def _live_price(self, contract, ticker: str = "") -> Optional[float]:
        """Precio por streaming con espera acotada (nunca se cuelga). None si no hay datos."""
        ib = await self._market_ib(ticker)
        tick = ib.reqMktData(contract, "", False, False)
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
            ib.cancelMktData(contract)

    async def _last_close(self, contract, use_rth: bool = True, kind: str = "precio") -> Optional[float]:
        bars = await self._historical(contract, "1 W", "TRADES", use_rth=use_rth, kind=kind)
        return m.num(bars[-1].close) if bars else None

    # ---- subyacente -----------------------------------------------------------------------
    async def _stock(self, ticker: str) -> Stock | Index:
        if ticker in self._stocks:
            return self._stocks[ticker]
        self._require()
        inst = self.instruments.get(ticker)
        stock = (Index(inst.symbol, inst.exchange, inst.currency) if inst is not None
                 else Stock(m.ib_symbol(ticker), "SMART", "USD"))   # «BRK.B» -> «BRK B»
        qualified = await self.ib.qualifyContractsAsync(stock)
        if not qualified or not stock.conId:
            raise UnsupportedTickerError(f"Ticker no reconocido por IBKR: {ticker}")
        self._stocks[ticker] = stock
        return stock

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        if ticker in self._sector_cache:
            return self._sector_cache[ticker]
        if (inst := self.instruments.get(ticker)) is not None:
            await self._stock(ticker)      # comprueba que existe
            return (inst.sector, None)
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
        price = await self._live_price(stock, ticker)
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
        own = [it for it in stocks.items() if it[0] in self.instruments]
        rest = [it for it in stocks.items() if it[0] not in self.instruments]
        if rest:
            out.update(await self._underlying_batches(rest, self.ib))
        if own:
            out.update(await self._underlying_batches(own, await self._market_ib(own[0][0])))
        return out

    async def _underlying_batches(self, items: list, ib: IB) -> dict[str, UnderlyingQuote]:
        out: dict[str, UnderlyingQuote] = {}
        for i in range(0, len(items), self.PRICE_BATCH):
            chunk = items[i : i + self.PRICE_BATCH]
            # 106 = IV de opciones; en un índice no se espera: el precio basta
            ticks = {t: ib.reqMktData(s, "" if t in self.instruments else "106", False, False) for t, s in chunk}
            try:
                waited, step = 0.0, 0.25
                while waited < self.s.quote_wait_seconds:
                    if all(
                        m.num(tk.marketPrice()) is not None and (t in self.instruments or m.num(tk.impliedVolatility) is not None)
                        for t, tk in ticks.items()
                    ):
                        break
                    await asyncio.sleep(step)
                    waited += step
            finally:
                for _, s in chunk:
                    ib.cancelMktData(s)
            for t, tk in ticks.items():
                q = UnderlyingQuote(price=m.num(tk.marketPrice()), iv=m.num(tk.impliedVolatility))
                if q.price is not None or q.iv is not None:
                    out[t] = q
        return out

    async def get_option_chain(self, ticker: str) -> OptionChain:
        stock = await self._stock(ticker)
        chains = await self.ib.reqSecDefOptParamsAsync(stock.symbol, "", stock.secType, stock.conId)
        inst = self.instruments.get(ticker)
        chain = m.pick_chain(chains, ticker, inst.exchange if inst else "SMART", inst.trading_class if inst else None, inst.multiplier if inst else 100)
        if chain is None:
            raise UnsupportedTickerError(f"Sin cadena de opciones para {ticker}")
        expiries = sorted(m.parse_expiry(e) for e in chain.expirations)
        return OptionChain(ticker, expiries, sorted(float(s) for s in chain.strikes), int(chain.multiplier or (inst.multiplier if inst else 100)))

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        if ticker in self.instruments:      # un índice no reparte dividendos
            return None
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
            if t in self.instruments:
                continue
            try:
                stocks[t] = await self._stock(t)
            except DataUnavailableError:
                continue
        out: dict[str, Optional[int]] = {t: None for t in tickers if t in self.instruments}
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
    def _ib_option(self, c: OptionContract) -> Option:
        inst = self.instruments.get(c.ticker)
        if inst is not None:
            opt = Option(inst.symbol, m.format_expiry(c.expiry), c.strike, c.right.value, inst.exchange,
                         multiplier=str(c.multiplier), currency=inst.currency, tradingClass=inst.trading_class)
        else:
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
        own = [c for c in contracts if c.ticker in self.instruments]
        rest = [c for c in contracts if c.ticker not in self.instruments]
        out = await self._quotes_group(rest, self.ib) if rest else {}
        if own:
            out.update(await self._instrument_quotes(own))
        return out

    async def _instrument_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        """Cotizaciones de los instrumentos: por la conexión de datos diferidos (open interest, delta y, con el mercado abierto, bid/ask). Con el mercado cerrado
        el diferido no trae bid/ask: a los contratos que llegan sin bid ni ask se les pide el último con la conexión principal (datos congelados, sin open
        interest) y se completan; el open interest y la delta del primer intento se conservan."""
        ib = await self._market_ib(contracts[0].ticker)
        got = await self._quotes_group(contracts, ib)
        if ib is self.ib:
            return got
        empty = [c for c, q in got.items() if q.bid is None and q.ask is None]
        if not empty:
            return got
        out = dict(got)
        for c, q2 in (await self._quotes_group(empty, self.ib)).items():
            q1 = out[c]
            out[c] = replace(q1, bid=q2.bid, ask=q2.ask, last=q1.last if q1.last is not None else q2.last,
                             bid_size=q1.bid_size if q1.bid_size is not None else q2.bid_size,
                             delta=q1.delta if q1.delta is not None else q2.delta, iv=q1.iv if q1.iv is not None else q2.iv,
                             open_interest=q1.open_interest if q1.open_interest is not None else q2.open_interest)
        return out

    async def _quotes_group(self, contracts: Sequence[OptionContract], ib: IB) -> dict[OptionContract, OptionQuote]:
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

        ib.errorEvent += on_error
        try:
            with _quiet(_COMPETING_FILTER):
                tickers = {c: ib.reqMktData(o, "101", False, False) for c, o in valid}  # 101 = open interest
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
                        ib.cancelMktData(o)
        finally:
            ib.errorEvent -= on_error
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

    async def get_bid_sizes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, int]:
        """Tamaño del bid de TWS (solo lectura, `reqMktData`): espera hasta `quote_wait_seconds`, pero sale en cuanto todos tienen
        bid y tamaño. Los contratos que no existen o no responden no aparecen."""
        self._require()
        pairs = [(c, self._ib_option(c)) for c in contracts]
        todo = [o for _, o in pairs if not o.conId]
        if todo:
            with _quiet_unknown_contracts():
                await self.ib.qualifyContractsAsync(*todo)
        valid = [(c, o) for c, o in pairs if o.conId]
        out: dict[OptionContract, int] = {}
        with _quiet(_COMPETING_FILTER):
            tickers = {c: self.ib.reqMktData(o, "", False, False) for c, o in valid}
            try:
                waited, step = 0.0, 0.25
                while waited < self.s.quote_wait_seconds and not all(
                    m.num(t.bid) is not None and m.num(t.bidSize) is not None for t in tickers.values()
                ):
                    await asyncio.sleep(step)
                    waited += step
            finally:
                for _, o in valid:
                    self.ib.cancelMktData(o)
        for c, t in tickers.items():
            size = m.num(t.bidSize)
            if size is not None:
                out[c] = int(size)
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
