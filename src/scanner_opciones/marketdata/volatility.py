"""Puerto `VolatilityProvider`: IV Rank e IV Percentile ya calculados por un proveedor externo.

Los precios, las cotizaciones de opciones y el margen siguen viniendo del broker (`BrokerGateway`);
esta fuente solo sustituye al cálculo local con el historial de IV de IBKR, que obliga a esperar el
límite de peticiones históricas. Nada fuera de las implementaciones reales debe importar el SDK del
proveedor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class IVMetrics:
    iv_rank: Optional[float] = None         # 0-100
    iv_percentile: Optional[float] = None   # 0-100


class VolatilityProvider(Protocol):
    async def get_iv_metrics(self, tickers: list[str]) -> dict[str, IVMetrics]:
        """Métricas de todos los tickers en una sola petición. Los que el proveedor no cubre
        no aparecen en el resultado. Lanza `VolatilityError` si el proveedor falla."""
        ...


@dataclass
class FakeVolatility:
    """Proveedor en memoria para los tests."""
    metrics: dict[str, IVMetrics] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[list[str]] = field(default_factory=list)

    async def get_iv_metrics(self, tickers: list[str]) -> dict[str, IVMetrics]:
        self.calls.append(list(tickers))
        if self.error is not None:
            raise self.error
        return {t: self.metrics[t] for t in tickers if t in self.metrics}
