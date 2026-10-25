"""Respaldo de Yahoo para el balance (cuando SEC EDGAR no tiene la empresa), YoY de los gráficos y línea de calidad de tastytrade."""
from datetime import date

import pandas as pd
import pytest

from scanner_opciones.domain.errors import FinancialsError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.financials import FakeFinancials, Financials
from scanner_opciones.marketdata.history import AnnualYear, EarningsQuarter, annual_growth, eps_growth, growth_pct
from scanner_opciones.marketdata.history_html import annual_svg, history_html
from scanner_opciones.marketdata.quality_card import quality_html, quality_text
from scanner_opciones.marketdata.yahoo_financials import FallbackFinancials, financials_from_statements, statements_currency

TODAY = date(2026, 10, 25)


def frame(rows: dict, *ends: str) -> pd.DataFrame:
    """DataFrame como los de yfinance: filas = conceptos, columnas = fechas (de la más reciente a la más antigua)."""
    return pd.DataFrame({pd.Timestamp(e): [r[i] for r in rows.values()] for i, e in enumerate(ends)}, index=list(rows))


QUARTERS = ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")
YEARS = ("2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31")


def statements(scale: float = 1.0) -> dict:
    return {
        "balance_q": frame({"Total Assets": [1000, 0, 0, 0], "Total Liabilities Net Minority Interest": [800, 0, 0, 0],
                            "Stockholders Equity": [200, 0, 0, 0], "Total Debt": [100, 0, 0, 0],
                            "Current Debt And Capital Lease Obligation": [20, 0, 0, 0], "Cash And Cash Equivalents": [60, 0, 0, 0]}, *QUARTERS),
        "balance_a": None,
        "cash_q": frame({"Operating Cash Flow": [30, 20, 25, 25], "Capital Expenditure": [-5, -5, -5, -5]}, *QUARTERS),
        "cash_a": None,
        "income_q": frame({"Operating Income": [40, 40, 40, 40], "Interest Expense": [5, 5, 5, 5], "Tax Provision": [10, 10, 10, 10],
                           "Pretax Income": [50, 50, 50, 50]}, *QUARTERS),
        "income_a": frame({"Total Revenue": [500, 450, 400, 380, 350], "Net Income": [100, 90, 80, 70, 60],
                           "Diluted Average Shares": [95, 100, 100, 100, 100]}, *YEARS),
    }


def test_statements_give_the_same_ratios_edgar_would():
    fin = financials_from_statements(statements(), TODAY)
    assert fin.liabilities_to_equity == pytest.approx(4.0)                        # 800 / 200
    assert fin.fcf_ttm == pytest.approx(100 - 20)                                 # (30+20+25+25) − 4 × 5
    assert fin.debt_to_equity == pytest.approx(0.5) and fin.period_end == date(2026, 6, 30)
    assert fin.interest_coverage == pytest.approx(160 / 20) and fin.cash_to_short_debt == pytest.approx(3.0)
    assert fin.ocf_to_debt == pytest.approx(1.0) and fin.capex_to_ocf == pytest.approx(0.2) and fin.fcf_to_assets == pytest.approx(0.08)
    assert fin.net_buyback_pct == pytest.approx(5.0)                              # 100 -> 95 acciones
    assert fin.roic == pytest.approx(160 * (1 - 0.2) / 300)                       # tasa efectiva 20 %
    assert fin.fiscal_years == 5 and fin.loss_years == 0 and fin.revenue_drop_years == 0
    assert [(y.end.year, y.revenue, y.net_income, y.source) for y in fin.annual][-1] == (2025, 500, 100, "Yahoo Finance")


def test_old_or_empty_statements_give_nothing():
    old = {k: (None if v is None else v.rename(columns=lambda c: c - pd.Timedelta(days=900))) for k, v in statements().items()}
    assert financials_from_statements(old, TODAY) is None
    assert financials_from_statements({}, TODAY) is None


def test_the_declared_currency_is_checked_against_yahoo_info():
    st = statements()          # deuda 100 y flujo operativo 100
    brl = 0.2
    assert statements_currency(st, {"totalDebt": 100, "operatingCashflow": 100}, "BRL", brl, TODAY) == "BRL"        # misma cifra: en reales
    assert statements_currency(st, {"totalDebt": 500, "operatingCashflow": 500}, "BRL", brl, TODAY) == "USD"        # 5 veces menos: ya están en dólares (PBR)
    assert statements_currency(st, {}, "BRL", brl, TODAY) == "BRL"                                                  # sin pruebas manda lo declarado
    assert statements_currency(st, {"totalDebt": 500}, "EUR", 1.12, TODAY) == "EUR"                                 # cambio ≈ 1: no se distingue
    assert statements_currency(st, {"totalDebt": 500}, "USD", None, TODAY) == "USD"


class Boom:
    async def get_financials(self, tickers):
        raise FinancialsError("sin red")


async def test_fallback_only_asks_yahoo_for_what_edgar_lacks():
    edgar = FakeFinancials({"KO": Financials(1.9, 12e9), "XP": Financials()})
    yahoo = FakeFinancials({"XP": Financials(15.4, 2e9, currency="USD"), "KO": Financials(99.0, 1.0)})
    got = await FallbackFinancials(edgar, yahoo).get_financials(["KO", "XP", "ZZZ"])
    assert got["KO"].liabilities_to_equity == 1.9 and got["XP"].liabilities_to_equity == 15.4
    assert yahoo.calls == [("get_financials", ("XP", "ZZZ"))]                  # KO ya tenía datos de EDGAR; ZZZ (vacío en EDGAR) también se prueba


async def test_fallback_keeps_edgar_result_when_yahoo_fails_and_delegates_the_rest():
    edgar = FakeFinancials({"XP": Financials()})
    edgar.get_filing_alerts = lambda *a: "alertas"
    wrapped = FallbackFinancials(edgar, Boom())
    assert (await wrapped.get_financials(["XP"]))["XP"] == Financials()
    assert wrapped.get_filing_alerts() == "alertas"
    assert (await FallbackFinancials(edgar, None).get_financials(["XP"]))["XP"] == Financials()


def year(y, revenue, income):
    return AnnualYear(date(y, 12, 31), revenue, income)


def test_growth_needs_a_positive_base_and_consecutive_years():
    assert growth_pct(100, 112) == pytest.approx(12.0) and growth_pct(100, 80) == pytest.approx(-20.0)
    assert growth_pct(-5, 10) is None and growth_pct(0, 10) is None and growth_pct(None, 10) is None
    years = [year(2022, 100, 10), year(2023, 110, -5), year(2025, 150, 20)]        # falta 2024
    assert annual_growth(years) == [(None, None), (pytest.approx(10.0), pytest.approx(-150.0)), (None, None)] or \
        annual_growth(years)[1][0] == pytest.approx(10.0)
    assert annual_growth(years)[2] == (None, None)
    assert annual_growth([year(2023, 100, 10), year(2024, 120, 5)])[1] == (pytest.approx(20.0), pytest.approx(-50.0))


def test_annual_chart_labels_each_year_with_its_yoy_in_colour():
    svg = annual_svg([year(2023, 1e9, 1e8), year(2024, 1.2e9, 0.5e8), year(2025, 1.1e9, -1e7)])
    assert "Ing +20 %" in svg and "BN −50 %" in svg and "Ing −8 %" in svg
    assert "BN —" in svg                                                       # beneficio negativo el año actual: base no comparable para el siguiente
    assert "var(--green)" in svg and "var(--red)" in svg
    html = history_html([], None, TODAY, [year(2024, 1e9, 1e8), AnnualYear(date(2025, 12, 31), 2e9, 3e8, "Yahoo Finance")])
    assert "Yahoo Finance" in html and "SEC EDGAR" not in html and "interanual (YoY)" in html


def test_eps_growth_compares_the_last_quarter_with_the_same_one_a_year_earlier():
    quarters = [EarningsQuarter(date(2025, 5, 20), 2.0, 2.1), EarningsQuarter(date(2025, 8, 18), 2.2, 2.2), EarningsQuarter(date(2025, 11, 17), 2.3, 2.2),
                EarningsQuarter(date(2026, 2, 12), 2.4, 2.3), EarningsQuarter(date(2026, 5, 18), 2.5, 2.6)]
    g, before, last = eps_growth(quarters)
    assert g == pytest.approx(25.0) and before.day.year == 2025 and last.day.year == 2026
    assert eps_growth(quarters[1:]) is None and eps_growth([]) is None
    assert "+25,0 % interanual" in history_html(quarters, None, TODAY)


def test_quality_line_shows_only_what_tastytrade_gave():
    info = TickerInfo("XP", eps_ttm=2.00, positive_quarters=4, reported_quarters=4, market_cap=15.43e9, option_liquidity=3,
                      next_earnings=date(2026, 11, 16), eps_surprise_pct=-13.5)
    text = quality_text(info)
    assert text.startswith("Calidad (tastytrade): ") and "BPA 12 m 2,00 $" in text and "4 de 4" in text and "15,4 mil M $" in text
    assert "3/5" in text and "16/11/2026" in text and "-13,5 %" in text
    html = quality_html(info, TODAY)
    assert "en 22 d" in html and "tastytrade" in html
    assert quality_text(TickerInfo("ZZ")) == "" and quality_html(None, TODAY) == ""
    assert "Próximos resultados" not in quality_html(TickerInfo("ZZ", eps_ttm=1.0, next_earnings=date(2026, 1, 1)), TODAY)       # resultados pasados no se enseñan


def test_company_card_and_tooltip_include_the_tastytrade_quality_line():
    from datetime import datetime
    from scanner_opciones.app.service import AppService
    from scanner_opciones.broker.fake_gateway import FakeGateway
    from scanner_opciones.config.settings import Settings
    from scanner_opciones.storage.db import Database
    svc = AppService(FakeGateway(), Database(), Settings(), lambda: datetime(2026, 10, 25, 12, 0))
    svc.quality.save([TickerInfo("XP", eps_ttm=2.0, positive_quarters=4, reported_quarters=4, option_liquidity=3, next_earnings=date(2026, 11, 16))])
    card = svc.company_card("XP")
    assert "Calidad" in card and "en 22 d" in card and "BPA 12 m" in card
    assert "Calidad (tastytrade)" in svc.company_notes_map()["XP"]
    assert svc.company_card("ZZZ") == ""
