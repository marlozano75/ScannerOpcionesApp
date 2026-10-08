"""`TastytradeVolatility`: IV Rank e IV Percentile de tastytrade (API de solo lectura, OAuth) y, con la
misma sesión, el último precio de los subyacentes (`PriceProvider`) y sus cierres diarios (`CandleProvider`).

Es el único módulo que importa el SDK de tastytrade. Solo usa *market metrics* y *market data*: no
toca cuentas ni órdenes. tastytrade devuelve los valores como fracción (0,508 = 50,8 %): aquí se
pasan a porcentaje, igual que el resto de la aplicación.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Optional, Sequence

from scanner_opciones.domain.errors import CandleError, OptionDataError, PriceError, VolatilityError
from scanner_opciones.domain.models import OptionQuote
from scanner_opciones.marketdata.candles import DailyBars
from scanner_opciones.marketdata.options import MarketExtras, OptionListing, listing_key
from scanner_opciones.marketdata.volatility import IVMetrics

log = logging.getLogger(__name__)

BATCH_SIZE = 100   # símbolos por petición (van en la URL; en cotizaciones es el límite del endpoint)

Fetch = Callable[[Sequence[str]], Awaitable[Sequence[Any]]]
CandleFetch = Callable[[Sequence[str], int, float], Awaitable[dict[str, DailyBars]]]   # símbolos, días, espera por lote
CANDLE_BATCH_SIZE = 50   # suscripciones a velas por lote (con todos a la vez tastytrade corta: «subscription size too big»)


ChainFetch = Callable[[str], Awaitable[OptionListing]]                            # símbolo de tastytrade -> puts existentes
OptionQuotesFetch = Callable[[Sequence[str]], Awaitable[dict[str, OptionQuote]]]  # símbolos DXLink -> cotizaciones


def tasty_symbol(ticker: str) -> str:
    """Símbolo de tastytrade: las clases de acciones llevan «/» («PBR-A» y «BRK.B» -> «PBR/A», «BRK/B»)."""
    return re.sub(r"[.\-]", "/", ticker.strip())


def to_price(item: Any) -> Optional[float]:
    """Último precio de una cotización de tastytrade: `last`, o `mark` si no hay; None si no hay ninguno."""
    for value in (item.last, item.mark):
        if value is not None and float(value) > 0:
            return float(value)
    return None


def _pct(value: Any) -> Optional[float]:
    return None if value is None else round(float(value) * 100, 2)


def to_metrics(item: Any) -> Optional[IVMetrics]:
    """`None` si tastytrade no da ni rank ni percentil para ese símbolo."""
    rank = _pct(item.implied_volatility_index_rank)
    percentile = _pct(item.implied_volatility_percentile)
    if rank is None and percentile is None:
        return None
    return IVMetrics(iv_rank=rank, iv_percentile=percentile)


class TastytradeVolatility:
    def __init__(
        self, client_secret: str, refresh_token: str,
        fetch: Optional[Fetch] = None, fetch_quotes: Optional[Fetch] = None,
        fetch_candles: Optional[CandleFetch] = None,
        fetch_chain: Optional[ChainFetch] = None, fetch_option_quotes: Optional[OptionQuotesFetch] = None,
        quote_wait: float = 20.0, settle: float = 3.0, option_batch_size: int = 400,
    ) -> None:
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._fetch = fetch or self._sdk_fetch
        self._fetch_quotes = fetch_quotes or self._sdk_fetch_quotes
        self._fetch_candles = fetch_candles or self._sdk_fetch_candles
        self._fetch_chain = fetch_chain or self._sdk_fetch_chain
        self._fetch_option_quotes = fetch_option_quotes or self._sdk_fetch_option_quotes
        self._quote_wait, self._settle, self._option_batch = quote_wait, settle, option_batch_size
        self._session: Any = None

    def _open_session(self) -> Any:
        from tastytrade import Session

        if self._session is None:
            self._session = Session(self._client_secret, self._refresh_token)
        return self._session

    async def _sdk_fetch(self, symbols: Sequence[str]) -> Sequence[Any]:
        from tastytrade.metrics import get_market_metrics

        return await get_market_metrics(self._open_session(), symbols)

    async def _sdk_fetch_quotes(self, symbols: Sequence[str]) -> Sequence[Any]:
        from tastytrade.market_data import get_market_data_by_type

        return await get_market_data_by_type(self._open_session(), equities=symbols)

    async def _sdk_fetch_candles(self, symbols: Sequence[str], days: int, timeout: float) -> dict[str, DailyBars]:
        """Velas diarias por DXLink: un lote de suscripciones por vez; el lote termina cuando todos sus
        símbolos han enviado el final del snapshot o se agota `timeout` (símbolos desconocidos no responden)."""
        from tastytrade.dxfeed import Candle
        from tastytrade.streamer import DXLinkStreamer

        session = self._open_session()
        start = datetime.now() - timedelta(days=days)
        out: dict[str, DailyBars] = {}
        async with DXLinkStreamer(session) as streamer:
            for i in range(0, len(symbols), CANDLE_BATCH_SIZE):
                batch = list(symbols[i:i + CANDLE_BATCH_SIZE])
                pending = set(batch)
                bars: dict[str, dict[int, float]] = {s: {} for s in batch}
                await streamer.subscribe_candle(batch, "1d", start_time=start)

                async def read() -> None:
                    async for c in streamer.listen(Candle):
                        sym = c.event_symbol.split("{")[0]
                        if sym not in bars:
                            continue
                        if float(c.close) > 0:
                            bars[sym][c.time] = float(c.close)
                        if c.snapshot_end:
                            pending.discard(sym)
                            if not pending:
                                return

                try:
                    await asyncio.wait_for(read(), timeout=timeout)
                except asyncio.TimeoutError:
                    log.info("Velas: sin respuesta de %s", ", ".join(sorted(pending)))
                for sym in batch:
                    await streamer.unsubscribe_candle(sym, "1d")
                    if bars[sym]:
                        out[sym] = [(datetime.fromtimestamp(ms / 1000).date(), px) for ms, px in sorted(bars[sym].items())]
        return out

    async def _sdk_fetch_chain(self, symbol: str) -> OptionListing:
        from tastytrade.instruments import OptionType, get_option_chain

        out: OptionListing = {}
        for expiry, options in (await get_option_chain(self._open_session(), symbol)).items():
            for o in options:
                # solo opciones estándar: las ajustadas por splits/fusiones (otra entrega) no son las de IBKR
                if o.option_type is OptionType.PUT and o.shares_per_contract == 100 and o.active:
                    out.setdefault(listing_key(expiry, float(o.strike_price)), o.streamer_symbol)
        return out

    async def _sdk_fetch_option_quotes(self, symbols: Sequence[str]) -> dict[str, OptionQuote]:
        """Quote + Greeks + Summary por DXLink. Un lote de suscripciones por vez: termina cuando todos tienen
        cotización y griegas, cuando pasan `settle` s sin datos nuevos (el resto no cotiza) o al agotar `wait`."""
        from tastytrade.dxfeed import Greeks, Quote, Summary
        from tastytrade.streamer import DXLinkStreamer

        got: dict[str, dict] = {s: {} for s in symbols}

        def num(x: Any) -> Optional[float]:
            try:
                v = float(x)
            except (TypeError, ValueError):
                return None
            return v if v == v and abs(v) != float("inf") else None

        def fill_quote(d: dict, e: Any) -> None:
            d.update(bid=num(e.bid_price), ask=num(e.ask_price), bid_size=num(e.bid_size), quoted=True)

        def fill_greeks(d: dict, e: Any) -> None:
            d.update(iv=num(e.volatility), delta=num(e.delta), greeks=True)

        def fill_summary(d: dict, e: Any) -> None:
            d.update(oi=num(e.open_interest))

        async with DXLinkStreamer(self._open_session()) as streamer:
            async def read(event_class: Any, fill: Callable[[dict, Any], None]) -> None:
                async for ev in streamer.listen(event_class):
                    if ev.event_symbol in got:
                        fill(got[ev.event_symbol], ev)

            tasks = [asyncio.create_task(read(c, f))
                     for c, f in ((Quote, fill_quote), (Greeks, fill_greeks), (Summary, fill_summary))]
            try:
                loop = asyncio.get_running_loop()
                for i in range(0, len(symbols), self._option_batch):
                    batch = list(symbols[i:i + self._option_batch])
                    for cls in (Quote, Greeks, Summary):
                        await streamer.subscribe(cls, batch)
                    start = last_change = loop.time()
                    last_n = -1
                    while loop.time() - start < self._quote_wait:
                        n = sum(1 for s in batch if got[s].get("quoted") and got[s].get("greeks"))
                        if n != last_n:
                            last_n, last_change = n, loop.time()
                        if n == len(batch) or (n > 0 and loop.time() - last_change > self._settle):
                            break
                        await asyncio.sleep(0.25)
                    for cls in (Quote, Greeks, Summary):
                        await streamer.unsubscribe(cls, batch)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, OptionQuote] = {}
        for s, d in got.items():
            if not (d.get("quoted") or d.get("greeks")):
                continue
            oi, bs = d.get("oi"), d.get("bid_size")
            out[s] = OptionQuote(
                bid=d.get("bid"), ask=d.get("ask"), last=None, delta=d.get("delta"), iv=d.get("iv"),
                open_interest=int(oi) if oi is not None else None, bid_size=int(bs) if bs is not None else None,
            )
        return out

    async def get_put_listing(self, ticker: str) -> OptionListing:
        try:
            return await self._fetch_chain(tasty_symbol(ticker))
        except Exception as exc:   # el SDK lanza tipos variados (red, autenticación, formato)
            self._session = None
            raise OptionDataError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc

    async def get_option_quotes(self, symbols: Sequence[str]) -> dict[str, OptionQuote]:
        if not symbols:
            return {}
        try:
            return await self._fetch_option_quotes(list(symbols))
        except Exception as exc:
            self._session = None
            raise OptionDataError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc

    async def get_market_extras(self, tickers: list[str]) -> dict[str, MarketExtras]:
        """IV a 30 días y fecha ex-dividendo, de las mismas *market metrics* que el IV Rank."""
        names = {tasty_symbol(t): t for t in tickers}
        symbols = list(names)
        out: dict[str, MarketExtras] = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]
            try:
                items = await self._fetch(batch)
            except Exception as exc:
                self._session = None
                raise VolatilityError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc
            for item in items:
                if item.symbol not in batch:
                    continue
                iv = getattr(item, "implied_volatility_index", None)
                out[names[item.symbol]] = MarketExtras(
                    iv30=float(iv) if iv is not None else None,
                    ex_dividend=getattr(item, "dividend_ex_date", None),
                )
        return out

    async def get_daily_closes(self, tickers: list[str], days: int, timeout: float = 20.0) -> dict[str, DailyBars]:
        symbols = {tasty_symbol(t): t for t in tickers}
        try:
            raw = await self._fetch_candles(list(symbols), days, timeout)
        except Exception as exc:   # el SDK lanza tipos variados (red, autenticación, formato)
            self._session = None
            raise CandleError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc
        return {symbols[s]: bars for s, bars in raw.items() if s in symbols}

    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        names = {tasty_symbol(t): t for t in tickers}          # «PBR-A» -> «PBR/A»; el resultado vuelve con el ticker de la app
        symbols = list(names)
        out: dict[str, float] = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]
            try:
                items = await self._fetch_quotes(batch)
            except Exception as exc:   # el SDK lanza tipos variados (red, autenticación, formato)
                self._session = None
                raise PriceError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc
            for item in items:
                if item.symbol in batch and (price := to_price(item)) is not None:
                    out[names[item.symbol]] = price
        return out

    async def get_iv_metrics(self, tickers: list[str]) -> dict[str, IVMetrics]:
        names = {tasty_symbol(t): t for t in tickers}          # «PBR-A» -> «PBR/A»; el resultado vuelve con el ticker de la app
        symbols = list(names)
        out: dict[str, IVMetrics] = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]
            try:
                items = await self._fetch(batch)
            except VolatilityError:
                raise
            except Exception as exc:   # el SDK lanza tipos variados (red, autenticación, formato)
                self._session = None   # que el siguiente intento abra sesión de nuevo
                raise VolatilityError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc
            for item in items:
                if (metrics := to_metrics(item)) is not None and item.symbol in batch:
                    out[names[item.symbol]] = metrics
        missing = [t for t in tickers if t not in out]
        if missing:
            log.info("tastytrade no devuelve IV Rank/Percentile de: %s", ", ".join(missing))
        return out
