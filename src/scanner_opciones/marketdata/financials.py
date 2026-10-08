"""Puerto `FinancialsProvider`: apalancamiento y flujo de caja de las empresas (balance y estado de flujos de
efectivo) de un proveedor externo. La implementación real es SEC EDGAR (`edgar.py`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol


@dataclass(frozen=True)
class Financials:
    liabilities_to_equity: Optional[float] = None   # pasivo total / patrimonio; None si el patrimonio es ≤ 0 o falta
    fcf_ttm: Optional[float] = None                 # flujo de caja libre (operativo − inversión en inmovilizado), USD, 12 meses
    period_end: Optional[date] = None               # fin del último periodo contable usado


class FinancialsProvider(Protocol):
    async def get_financials(self, tickers: list[str]) -> dict[str, Financials]:
        """Un resultado por ticker consultado con éxito; los que el proveedor no conoce traen `Financials()` vacío.
        Lanza `FinancialsError` si el proveedor falla o nos bloquea."""
        ...


@dataclass
class FakeFinancials:
    """Proveedor en memoria para los tests."""
    data: dict[str, Financials] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[tuple] = field(default_factory=list)

    async def get_financials(self, tickers: list[str]) -> dict[str, Financials]:
        self.calls.append(("get_financials", tuple(tickers)))
        if self.error is not None:
            raise self.error
        return {t: self.data.get(t, Financials()) for t in tickers}
