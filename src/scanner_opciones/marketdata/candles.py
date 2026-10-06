"""Puerto `CandleProvider`: cierres diarios de los subyacentes (para la tendencia), sin pasar por el
límite de peticiones históricas de IBKR."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol

DailyBars = list[tuple[date, float]]   # (día, cierre), orden ascendente


class CandleProvider(Protocol):
    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, DailyBars]:
        """Cierres diarios de los últimos `days` días naturales de cada ticker. Los que el proveedor no
        cubre no aparecen. Lanza `CandleError` si el proveedor falla."""
        ...


@dataclass
class FakeCandles:
    """Proveedor en memoria para los tests."""
    bars: dict[str, DailyBars] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[list[str]] = field(default_factory=list)
    days: list[int] = field(default_factory=list)       # `days` pedido en cada llamada

    async def get_daily_closes(self, tickers: list[str], days: int) -> dict[str, DailyBars]:
        self.calls.append(list(tickers))
        self.days.append(days)
        if self.error is not None:
            raise self.error
        return {t: self.bars[t] for t in tickers if t in self.bars}
