"""`TastytradeVolatility`: IV Rank e IV Percentile de tastytrade (API de solo lectura, OAuth).

Es el único módulo que importa el SDK de tastytrade. Solo usa el endpoint de *market metrics*: no
toca cuentas ni órdenes. tastytrade devuelve los valores como fracción (0,508 = 50,8 %): aquí se
pasan a porcentaje, igual que el resto de la aplicación.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Sequence

from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.marketdata.volatility import IVMetrics

log = logging.getLogger(__name__)

BATCH_SIZE = 100   # símbolos por petición (van en la URL)

Fetch = Callable[[Sequence[str]], Awaitable[Sequence[Any]]]


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
    def __init__(self, client_secret: str, refresh_token: str, fetch: Optional[Fetch] = None) -> None:
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._fetch = fetch or self._sdk_fetch
        self._session: Any = None

    async def _sdk_fetch(self, symbols: Sequence[str]) -> Sequence[Any]:
        from tastytrade import Session
        from tastytrade.metrics import get_market_metrics

        if self._session is None:
            self._session = Session(self._client_secret, self._refresh_token)
        return await get_market_metrics(self._session, symbols)

    async def get_iv_metrics(self, tickers: list[str]) -> dict[str, IVMetrics]:
        out: dict[str, IVMetrics] = {}
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i:i + BATCH_SIZE]
            try:
                items = await self._fetch(batch)
            except VolatilityError:
                raise
            except Exception as exc:   # el SDK lanza tipos variados (red, autenticación, formato)
                self._session = None   # que el siguiente intento abra sesión de nuevo
                raise VolatilityError(f"tastytrade: {type(exc).__name__}: {str(exc)[:200]}") from exc
            for item in items:
                if (metrics := to_metrics(item)) is not None and item.symbol in batch:
                    out[item.symbol] = metrics
        missing = [t for t in tickers if t not in out]
        if missing:
            log.info("tastytrade no devuelve IV Rank/Percentile de: %s", ", ".join(missing))
        return out
