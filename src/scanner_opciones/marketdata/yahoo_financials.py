"""Balance y flujo de caja desde los estados financieros de Yahoo Finance, como RESPALDO de SEC EDGAR (empresas que EDGAR no tiene o cuyas cifras
son viejas: XP publica en EDGAR hasta 2024). Mismas definiciones que `edgar.py` (`Financials`); la lógica es pura sobre DataFrames de `yfinance`
y va aquí, mientras que la descarga (`YahooFinancials`) está en `yahoo.py`, el único módulo que importa la librería."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Mapping, Optional

from scanner_opciones.domain.errors import FinancialsError
from scanner_opciones.marketdata.edgar import ANNUAL_RESULT_YEARS, STALE_DAYS, TAX_FALLBACK, profit_stability, to_usd
from scanner_opciones.marketdata.financials import NO_LIMIT, Financials, FinancialsProvider
from scanner_opciones.marketdata.fx import RateAt
from scanner_opciones.marketdata.history import AnnualYear

log = logging.getLogger(__name__)

SOURCE = "Yahoo Finance"


def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _row(frame: Any, *names: str) -> dict[date, float]:
    """{fin del periodo: valor} de la primera fila con datos (las columnas de yfinance son fechas; None/NaN se ignoran)."""
    if frame is None or getattr(frame, "empty", True):
        return {}
    for name in names:
        if name in frame.index:
            values = {}
            for stamp, v in frame.loc[name].items():
                if (n := _num(v)) is not None:
                    values[stamp.date() if hasattr(stamp, "date") else stamp] = n
            if values:
                return values
    return {}


def _latest(values: Mapping[date, float]) -> Optional[tuple[date, float]]:
    return max(values.items()) if values else None


def _ttm(quarterly: Mapping[date, float], annual: Mapping[date, float]) -> Optional[tuple[float, date]]:
    """Flujo de 12 meses: la suma de los 4 últimos trimestres seguidos (≈ 9 meses entre el primero y el último) o, sin ellos, el último año fiscal."""
    ends = sorted(quarterly)[-4:]
    if len(ends) == 4 and 240 <= (ends[-1] - ends[0]).days <= 290:
        return sum(quarterly[e] for e in ends), ends[-1]
    last = _latest(annual)
    return (last[1], last[0]) if last else None


def financials_from_statements(stmts: Mapping[str, Any], today: date) -> Optional[Financials]:
    """`Financials` (en la moneda de las cuentas) de los estados de Yahoo: `balance_q/balance_a`, `cash_q/cash_a`, `income_q/income_a`
    (DataFrames de yfinance), o None si no hay cifras recientes."""
    fresh = today - timedelta(days=STALE_DAYS)
    bq, ba = stmts.get("balance_q"), stmts.get("balance_a")

    def balance(*names: str) -> Optional[float]:
        for frame in (bq, ba):
            last = _latest(_row(frame, *names))
            if last is not None:
                return last[1] if last[0] >= fresh else None
        return None

    def balance_end() -> Optional[date]:
        ends = [last[0] for frame in (bq, ba) if (last := _latest(_row(frame, "Total Assets", "Total Liabilities Net Minority Interest")))]
        return max(ends) if ends else None

    def flow(frame_q: str, frame_a: str, *names: str) -> Optional[tuple[float, date]]:
        r = _ttm(_row(stmts.get(frame_q), *names), _row(stmts.get(frame_a), *names))
        return r if r is not None and r[1] >= fresh else None

    end_balance = balance_end()
    equity = balance("Total Equity Gross Minority Interest", "Stockholders Equity", "Common Stock Equity")
    assets = balance("Total Assets")
    liabilities = balance("Total Liabilities Net Minority Interest")
    if liabilities is None and assets is not None and equity is not None:
        liabilities = assets - equity
    ratio = liabilities / equity if equity is not None and equity > 0 and liabilities is not None and liabilities >= 0 else None

    ocf, capex = flow("cash_q", "cash_a", "Operating Cash Flow"), flow("cash_q", "cash_a", "Capital Expenditure")
    ocf_val = ocf[0] if ocf else None
    capex_val = abs(capex[0]) if capex else None
    fcf = ocf_val - capex_val if ocf_val is not None and capex_val is not None else None
    flow_end = ocf[1] if fcf is not None else None

    debt = balance("Total Debt")
    if debt is None:      # sin deuda publicada: se da por «sin deuda» solo si el balance lo hace creíble (como en EDGAR)
        credible = equity is not None and equity > 0 and liabilities is not None and liabilities / equity < 1.5
        debt = 0.0 if credible else None
        current = 0.0 if credible else None
    else:
        current = balance("Current Debt And Capital Lease Obligation", "Current Debt") or 0.0
    op = flow("income_q", "income_a", "Operating Income")
    interest = flow("income_q", "income_a", "Interest Expense", "Interest Expense Non Operating")
    cash = balance("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")

    out: dict[str, Optional[float]] = dict.fromkeys(
        ("debt_to_equity", "interest_coverage", "cash_to_short_debt", "ocf_to_debt", "capex_to_ocf", "fcf_to_assets", "net_buyback_pct",
         "roic", "loss_years", "fiscal_years", "revenue_drop_years", "revenue_years", "earnings_volatility"))
    if debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = debt / equity
    if debt is not None and ocf_val is not None:
        out["ocf_to_debt"] = NO_LIMIT if debt <= 0 else min(ocf_val / debt, NO_LIMIT)
    if debt is not None and debt <= 0:
        out["interest_coverage"] = NO_LIMIT
    elif op is not None and interest is not None:
        out["interest_coverage"] = NO_LIMIT if abs(interest[0]) < 1e-9 else min(op[0] / abs(interest[0]), NO_LIMIT)
    if cash is not None and current is not None:
        out["cash_to_short_debt"] = NO_LIMIT if current <= 0 else min(cash / current, NO_LIMIT)
    if ocf_val is not None and capex_val is not None:
        out["capex_to_ocf"] = NO_LIMIT if ocf_val <= 0 else capex_val / ocf_val
    if fcf is not None and assets is not None and assets > 0:
        out["fcf_to_assets"] = fcf / assets

    income_a = stmts.get("income_a")
    shares = sorted(_row(income_a, "Diluted Average Shares").items())
    if len(shares) >= 2 and shares[-1][0] >= fresh and shares[-2][1] and 350 <= (shares[-1][0] - shares[-2][0]).days <= 380:
        out["net_buyback_pct"] = -(shares[-1][1] / shares[-2][1] - 1.0) * 100.0

    if op is not None and debt is not None and equity is not None and debt + equity > 0:
        tax, pretax = flow("income_q", "income_a", "Tax Provision"), flow("income_q", "income_a", "Pretax Income")
        rate = TAX_FALLBACK
        if tax is not None and pretax is not None and pretax[0] > 0 and 0 <= tax[0] / pretax[0] <= 0.40:
            rate = tax[0] / pretax[0]
        out["roic"] = (op[0] * (1.0 - rate) if op[0] > 0 else op[0]) / (debt + equity)

    revenue_by_year = _row(income_a, "Total Revenue", "Operating Revenue")
    income_by_year = _row(income_a, "Net Income", "Net Income Common Stockholders", "Net Income Including Noncontrolling Interests")
    years = sorted(set(revenue_by_year) | set(income_by_year))
    annual: tuple[AnnualYear, ...] = ()
    if years and years[-1] >= fresh:
        annual = tuple(AnnualYear(e, revenue_by_year.get(e), income_by_year.get(e), SOURCE) for e in years[-ANNUAL_RESULT_YEARS:])
        out.update(profit_stability([(e.isoformat(), v) for e, v in sorted(income_by_year.items())],
                                    [(e.isoformat(), v) for e, v in sorted(revenue_by_year.items())]))
    if ratio is None and fcf is None and all(v is None for v in out.values()) and not annual:
        return None
    return Financials(ratio, fcf, flow_end or end_balance, annual=annual, **out)


AMBIGUOUS_RATE = (0.7, 1.4)    # con un cambio parecido a 1 (EUR, GBP) no se distingue «en su moneda» de «en dólares» por magnitud: se fía de Yahoo


def statements_currency(stmts: Mapping[str, Any], info: Mapping[str, Any], declared: Optional[str], rate: Optional[float], today: date) -> str:
    """Moneda en que vienen realmente los estados de Yahoo. `financialCurrency` a veces miente: los de PBR llegan en dólares aunque diga BRL, y
    convertirlos otra vez los dividiría por ~5. Se compara la deuda total y el flujo operativo de las cuentas con los que Yahoo da en `info` (siempre
    en `financialCurrency`): si el cociente se parece al tipo de cambio y no a 1, las cuentas están en dólares. Sin pruebas, manda `declared`."""
    if not declared or declared == "USD":
        return "USD"
    if rate is None or AMBIGUOUS_RATE[0] <= rate <= AMBIGUOUS_RATE[1]:
        return declared
    fresh = today - timedelta(days=STALE_DAYS)
    debt = _latest(_row(stmts.get("balance_q"), "Total Debt")) or _latest(_row(stmts.get("balance_a"), "Total Debt"))
    ocf = _ttm(_row(stmts.get("cash_q"), "Operating Cash Flow"), _row(stmts.get("cash_a"), "Operating Cash Flow"))
    pairs = [(debt[1] if debt and debt[0] >= fresh else None, info.get("totalDebt")),
             (ocf[0] if ocf and ocf[1] >= fresh else None, info.get("operatingCashflow"))]
    votes = []
    for stmt, ref in pairs:
        stmt, ref = _num(stmt), _num(ref)
        if stmt and ref and stmt > 0 and ref > 0:
            ratio = stmt / ref
            votes.append("USD" if abs(math.log(ratio / rate)) < abs(math.log(ratio)) else declared)
    return "USD" if votes.count("USD") > len(votes) / 2 else declared


def is_empty(fin: Optional[Financials]) -> bool:
    return fin is None or fin == Financials()


def convert(fin: Financials, currency: Optional[str], rate_at: Optional[RateAt]) -> Financials:
    """Pasa a dólares los importes de unas cuentas en `currency` (los cocientes no cambian)."""
    return fin if not currency or currency == "USD" else to_usd(fin, currency, rate_at)


class FallbackFinancials:
    """`FinancialsProvider` que pide a `backup` (Yahoo) lo que `primary` (EDGAR) no tiene: los tickers que devuelve vacíos. Todo lo demás
    (alertas de la SEC, sector) lo sigue dando `primary`."""

    def __init__(self, primary: FinancialsProvider, backup: Optional[FinancialsProvider]) -> None:
        self.primary, self.backup = primary, backup

    def __getattr__(self, name: str):
        return getattr(self.primary, name)

    async def get_financials(self, tickers: list[str]) -> dict[str, Financials]:
        found = await self.primary.get_financials(tickers)
        missing = [t for t in tickers if t in found and is_empty(found[t])]
        if missing and self.backup is not None:
            try:
                extra = await self.backup.get_financials(missing)
            except FinancialsError as exc:
                log.warning("Balance de respaldo (Yahoo) no disponible: %s", exc)
                return found
            rescued = [t for t in missing if not is_empty(extra.get(t))]
            for t in rescued:
                found[t] = extra[t]
            if rescued:
                log.info("Balance: %d de %d tickers sin datos en SEC EDGAR se completan con Yahoo Finance (%s)",
                         len(rescued), len(missing), ", ".join(rescued[:12]))
        return found
