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
