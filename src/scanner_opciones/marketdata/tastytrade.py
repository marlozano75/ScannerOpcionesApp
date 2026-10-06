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

from scanner_opciones.domain.errors import CandleError, PriceError, VolatilityError
from scanner_opciones.marketdata.candles import DailyBars
from scanner_opciones.marketdata.volatility import IVMetrics

log = logging.getLogger(__name__)

BATCH_SIZE = 100   # símbolos por petición (van en la URL; en cotizaciones es el límite del endpoint)

Fetch = Callable[[Sequence[str]], Awaitable[Sequence[Any]]]
CandleFetch = Callable[[Sequence[str], int, float], Awaitable[dict[str, DailyBars]]]   # símbolos, días, espera por lote
CANDLE_BATCH_SIZE = 50   # suscripciones a velas por lote (con todos a la vez tastytrade corta: «subscription size too big»)


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
    ) -> None:
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._fetch = fetch or self._sdk_fetch
        self._fetch_quotes = fetch_quotes or self._sdk_fetch_quotes
        self._fetch_candles = fetch_candles or self._sdk_fetch_candles
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
