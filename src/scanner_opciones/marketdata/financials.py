"""Puerto `FinancialsProvider`: apalancamiento y flujo de caja de las empresas (balance y estado de flujos de
efectivo) de un proveedor externo. La implementación real es SEC EDGAR (`edgar.py`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol


NO_LIMIT = 999.0   # valor de un cociente cuando no hay deuda / intereses / deuda corriente que cubrir


@dataclass(frozen=True)
class Financials:
    liabilities_to_equity: Optional[float] = None   # pasivo total / patrimonio; None si el patrimonio es ≤ 0 o falta
    fcf_ttm: Optional[float] = None                 # flujo de caja libre (operativo − inversión en inmovilizado), USD, 12 meses
    period_end: Optional[date] = None               # fin del último periodo contable usado
    # solvencia y calidad del flujo de caja; NO_LIMIT = «sin deuda / sin intereses»: no hay nada que cubrir
    debt_to_equity: Optional[float] = None          # deuda financiera / patrimonio
    interest_coverage: Optional[float] = None       # resultado operativo / gastos por intereses (12 meses)
    cash_to_short_debt: Optional[float] = None      # efectivo / deuda a corto plazo
    ocf_to_debt: Optional[float] = None             # flujo de caja operativo / deuda total
    capex_to_ocf: Optional[float] = None            # inversión en inmovilizado / flujo de caja operativo
    fcf_to_assets: Optional[float] = None           # flujo de caja libre / activos totales
    net_buyback_pct: Optional[float] = None         # reducción del nº de acciones diluidas en el último año fiscal, en %
    roic: Optional[float] = None                    # resultado operativo tras impuestos / (deuda financiera + patrimonio), fracción
    loss_years: Optional[int] = None                # años fiscales con pérdidas de los últimos 10 (None con menos de 5 años de historia)
    fiscal_years: Optional[int] = None              # años fiscales evaluados (hasta 10)


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
