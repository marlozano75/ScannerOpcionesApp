"""EDGAR con empresas que no reportan en dólares (XP: reales): cocientes iguales, importes convertidos al cambio de cada cierre."""
from datetime import date, timedelta

import pytest

from scanner_opciones.marketdata.edgar import EdgarFinancialsProvider, parse_company_facts, reporting_currency
from scanner_opciones.marketdata.fx import FakeFx, FxError, rate_on

TODAY = date(2026, 10, 8)


def flow(start, end, val, form="20-F"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": end}


def inst(end, val):
    return {"end": end, "val": val, "form": "20-F", "filed": end}


def company(unit="BRL", scale=1.0):
    """Empresa IFRS con cifras en `unit`: patrimonio 100, pasivo 300 (cociente 3), flujo operativo 50, capex 10; 5 años de ingresos y beneficio."""
    years = [(f"{y}-01-01", f"{y}-12-31") for y in range(2021, 2026)]
    ifrs = {
        "Equity": inst("2025-12-31", 100.0 * scale), "Liabilities": inst("2025-12-31", 300.0 * scale),
        "CashFlowsFromUsedInOperatingActivities": flow(*years[-1], 50.0 * scale),
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": flow(*years[-1], 10.0 * scale),
    }
    out = {tag: {"units": {unit: [f]}} for tag, f in ifrs.items()}
    out["Revenue"] = {"units": {unit: [flow(s, e, 1000.0 * scale * (1 + n)) for n, (s, e) in enumerate(years)]}}
    out["ProfitLoss"] = {"units": {unit: [flow(s, e, 100.0 * scale * (1 + n)) for n, (s, e) in enumerate(years)]}}
    return {"facts": {"ifrs-full": out}}


def test_the_reporting_currency_is_the_monetary_unit_with_most_facts():
    assert reporting_currency(company("BRL")["facts"]["ifrs-full"]) == "BRL"
    assert reporting_currency(company("USD")["facts"]["ifrs-full"]) == "USD"
    assert reporting_currency({"X": {"units": {"shares": [1], "BRL/shares": [1, 2]}}}) == "USD"     # sin unidades monetarias


def test_ratios_do_not_need_a_rate_and_amounts_are_left_empty_without_one():
    fin = parse_company_facts(company("BRL"), TODAY)
    assert fin.liabilities_to_equity == pytest.approx(3.0) and fin.currency == "BRL"
    assert fin.fcf_ttm is None and fin.annual == ()          # nunca reales presentados como dólares


def test_amounts_are_converted_at_the_rate_of_each_period_end():
    rates = {"BRL": lambda d: 0.2 if d.year == 2025 else 0.1}
    fin = parse_company_facts(company("BRL"), TODAY, lambda c, d: rates[c](d))
    assert fin.fcf_ttm == pytest.approx(40.0 * 0.2)           # (50 − 10) reales al cambio del cierre
    last, first = fin.annual[-1], fin.annual[0]
    assert last.revenue == pytest.approx(5000.0 * 0.2) and first.revenue == pytest.approx(1000.0 * 0.1)
    assert last.net_margin_pct == pytest.approx(first.net_margin_pct)       # el margen no depende de la moneda


def test_usd_companies_are_untouched_and_a_missing_rate_for_one_year_drops_only_that_year():
    usd = parse_company_facts(company("USD"), TODAY)
    assert usd.currency == "USD" and usd.fcf_ttm == 40.0 and usd.annual[-1].revenue == 5000.0
    partial = parse_company_facts(company("BRL"), TODAY, lambda c, d: 0.2 if d.year >= 2024 else None)
    assert [y.revenue is not None for y in partial.annual] == [False, False, False, True, True]     # solo pierde los años sin cambio


def test_rate_on_uses_the_last_quotation_within_ten_days():
    h = {date(2025, 12, 26): 0.18, date(2025, 12, 30): 0.2}
    assert rate_on(h, date(2025, 12, 31)) == 0.2 and rate_on(h, date(2025, 12, 28)) == 0.18
    assert rate_on(h, date(2025, 12, 20)) is None and rate_on(h, date(2026, 6, 1)) is None and rate_on({}, TODAY) is None


async def _provider(fx, data):
    async def fetch(url):
        return {"1": {"ticker": "XP", "cik_str": 7}} if "company_tickers" in url else data
    return EdgarFinancialsProvider("Prueba prueba@ejemplo.com", requests_per_second=1000, fetch_json=fetch, today=lambda: TODAY, fx=fx)


async def test_the_provider_asks_for_the_rate_once_per_currency_and_converts():
    fx = FakeFx({"BRL": 0.2})
    p = await _provider(fx, company("BRL"))
    got = await p.get_financials(["XP"])
    await p.get_financials(["XP"])
    assert got["XP"].fcf_ttm == pytest.approx(8.0) and fx.calls == ["BRL"]


async def test_the_provider_keeps_the_ratios_when_the_rate_fails_and_does_not_ask_fx_for_usd():
    p = await _provider(FakeFx(error=FxError("sin red")), company("BRL"))
    fin = (await p.get_financials(["XP"]))["XP"]
    assert fin.liabilities_to_equity == pytest.approx(3.0) and fin.fcf_ttm is None
    fx = FakeFx()
    await (await _provider(fx, company("USD"))).get_financials(["XP"])
    assert fx.calls == []
