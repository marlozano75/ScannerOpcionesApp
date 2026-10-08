"""Puerto `FundamentalsProvider`: datos de calidad de la empresa de un proveedor externo (tastytrade).

`get_fundamentals` sale de las mismas *market metrics* que el IV Rank (una petición por 100 tickers);
`get_quarterly_eps` baja el historial de resultados de cada ticker (una petición por ticker), por eso la
actualización diaria solo lo pide cuando está desactualizado. Nada fuera de las implementaciones reales debe importar
el SDK del proveedor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol

# tastytrade devuelve este valor cuando no tiene el P/E o el EPS de la empresa
MISSING_EPS_SENTINEL = -99999.0


@dataclass(frozen=True)
class Fundamentals:
    eps_ttm: Optional[float] = None
    market_cap: Optional[float] = None
    option_liquidity: Optional[int] = None
    next_earnings: Optional[date] = None
    eps_surprise_pct: Optional[float] = None


class FundamentalsProvider(Protocol):
    async def get_fundamentals(self, tickers: list[str]) -> dict[str, Fundamentals]:
        """Lanza `VolatilityError` si el proveedor falla."""
        ...

    async def get_quarterly_eps(self, tickers: list[str]) -> dict[str, list[float]]:
        """EPS de los últimos trimestres reportados (hasta 4, del más antiguo al más reciente). Los tickers que
        fallan o no tienen historial no aparecen."""
        ...


def clean_eps(value: Optional[float]) -> Optional[float]:
    """El EPS de tastytrade, sin los valores que significan «sin dato»: el centinela −99999,99 y el 0,0 exacto
    (BNY y CB venían así siendo rentables)."""
    if value is None or value <= MISSING_EPS_SENTINEL or value == 0.0:
        return None
    return value


def resolve_eps(eps_ttm: Optional[float], quarters: list[float]) -> Optional[float]:
    """EPS de 12 meses: el del proveedor si es válido y, si no, la suma de los 4 últimos trimestres reportados."""
    cleaned = clean_eps(eps_ttm)
    if cleaned is not None:
        return cleaned
    return sum(quarters) if len(quarters) >= 4 else None


@dataclass
class FakeFundamentals:
    """Proveedor en memoria para los tests."""
    fundamentals: dict[str, Fundamentals] = field(default_factory=dict)
    quarters: dict[str, list[float]] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[tuple] = field(default_factory=list)

    async def get_fundamentals(self, tickers: list[str]) -> dict[str, Fundamentals]:
        self.calls.append(("get_fundamentals", tuple(tickers)))
        if self.error is not None:
            raise self.error
        return {t: self.fundamentals[t] for t in tickers if t in self.fundamentals}

    async def get_quarterly_eps(self, tickers: list[str]) -> dict[str, list[float]]:
        self.calls.append(("get_quarterly_eps", tuple(tickers)))
        return {t: self.quarters[t] for t in tickers if t in self.quarters}
