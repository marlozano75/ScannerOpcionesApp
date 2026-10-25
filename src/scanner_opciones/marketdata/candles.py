"""Puerto `CandleProvider`: cierres diarios de los subyacentes (para la tendencia), sin pasar por el
límite de peticiones históricas de IBKR."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol

DailyBars = list[tuple[date, float]]   # (día, cierre), orden ascendente
OhlcBars = list[tuple[date, float, Optional[float], Optional[float]]]   # (día, cierre, máximo, mínimo); máx./mín. None si el proveedor no los da


class CandleProvider(Protocol):
    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, DailyBars]:
        """Cierres diarios de los últimos `days` días naturales de cada ticker. Los que el proveedor no
        cubre no aparecen. Lanza `CandleError` si el proveedor falla."""
        ...

    async def get_daily_ohlc(self, tickers: list[str], days: int) -> dict[str, OhlcBars]:
        """Como `get_daily_closes`, con el máximo y el mínimo de cada día (para el ATR)."""
        ...


@dataclass
class FakeCandles:
    """Proveedor en memoria para los tests."""
    bars: dict[str, DailyBars] = field(default_factory=dict)
    ranges: dict[str, dict[date, tuple[float, float]]] = field(default_factory=dict)   # ticker -> día -> (máximo, mínimo)
    error: Optional[Exception] = None
    calls: list[list[str]] = field(default_factory=list)
    days: list[int] = field(default_factory=list)       # `days` pedido en cada llamada

    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, DailyBars]:
        self.calls.append(list(tickers))
        self.days.append(days)
        if self.error is not None:
            raise self.error
        return {t: self.bars[t] for t in tickers if t in self.bars}

    async def get_daily_ohlc(self, tickers: list[str], days: int) -> dict[str, OhlcBars]:
        closes = await self.get_daily_closes(tickers, days)
        return {t: [(d, px, *self.ranges.get(t, {}).get(d, (px, px))) for d, px in bars] for t, bars in closes.items()}   # sin rangos: máx. = mín. = cierre


class SplitCandles:
    """Reparte las peticiones entre dos proveedores: los `tickers` de `other` van a `other` (p. ej. Yahoo para el Euro Stoxx 50) y el resto a `primary`.
    Un fallo de `other` no impide que `primary` devuelva lo suyo, y al revés se lanza el de `primary`."""

    def __init__(self, primary: CandleProvider, other: CandleProvider, tickers) -> None:
        self.primary = primary
        self.other = other
        self.tickers = frozenset(tickers)

    async def _split(self, method: str, tickers: list[str], days: int) -> dict:
        mine = [t for t in tickers if t not in self.tickers]
        theirs = [t for t in tickers if t in self.tickers]
        out: dict = {}
        if mine:
            out.update(await getattr(self.primary, method)(mine, days))
        if theirs:
            try:
                out.update(await getattr(self.other, method)(theirs, days))
            except Exception as exc:        # el histórico de un índice no debe impedir el del resto
                import logging
                logging.getLogger(__name__).warning("Velas de %s no disponibles: %s", ", ".join(theirs), exc)
        return out

    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, DailyBars]:
        return await self._split("get_daily_closes", tickers, days)

    async def get_daily_ohlc(self, tickers: list[str], days: int) -> dict[str, OhlcBars]:
        return await self._split("get_daily_ohlc", tickers, days)
