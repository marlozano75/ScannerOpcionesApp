"""Puerto `FinancialsProvider`: apalancamiento y flujo de caja de las empresas (balance y estado de flujos de
efectivo) de un proveedor externo. La implementación real es SEC EDGAR (`edgar.py`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol

from scanner_opciones.marketdata.history import AnnualYear


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
    revenue_drop_years: Optional[int] = None        # años en que bajaron los ingresos de los últimos 10 (None con < 5 años de historia)
    revenue_years: Optional[int] = None             # años comparados con el anterior (hasta 9)
    earnings_volatility: Optional[float] = None     # desviación típica del crecimiento anual del beneficio neto (fracción)
    net_buyback_usd: Optional[float] = None         # recompras de acciones − emisión de acciones nuevas, USD, 12 meses (negativo = emitió más)
    new_asset_return: Optional[float] = None        # Δ flujo operativo anual / Δ activos totales en dos años fiscales (0,30 = 30 ¢ por dólar)
    net_income_fy: Optional[float] = None           # beneficio neto del último año fiscal, USD (P/E del PEG histórico)
    earnings_cagr: Optional[float] = None           # crecimiento anual compuesto del beneficio neto entre 3 y 5 años (fracción; None si no es positivo)
    analyst_peg: Optional[float] = None             # PEG de Yahoo (`trailingPegRatio`): P/E sobre el crecimiento multianual que estiman los analistas
    currency: str = "USD"                         # moneda en que reporta la empresa; los importes (fcf_ttm, annual) ya van convertidos a USD
    annual: tuple[AnnualYear, ...] = ()             # ingresos y beneficio neto de los últimos años fiscales (del más antiguo al más reciente)


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
