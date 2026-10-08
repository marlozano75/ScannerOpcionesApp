"""SEC EDGAR: análisis de `companyfacts` (balance y flujo de caja de 12 meses) y proveedor."""
from datetime import date, timedelta

import pytest

from scanner_opciones.domain.errors import FinancialsError
from scanner_opciones.marketdata.edgar import EdgarFinancialsProvider, flow_ttm, parse_company_facts
from scanner_opciones.marketdata.financials import Financials

TODAY = date(2026, 10, 8)


def core(f):
    """Los tres campos básicos de `Financials` (los de solvencia se prueban aparte)."""
    return None if f is None else (f.liabilities_to_equity, f.fcf_ttm, f.period_end)


def inst(end, val, filed=None):
    return {"end": end, "val": val, "form": "10-Q", "filed": filed or end}


def flow(start, end, val, form="10-K", filed=None):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed or end}


def gaap(**tags):
    """companyfacts mínimo: cada argumento es una etiqueta us-gaap con sus hechos en USD."""
    return {"facts": {"us-gaap": {tag: {"units": {"USD": facts}} for tag, facts in tags.items()}}}


# año fiscal 2025 (cierra el 31-dic-2025) y acumulado de 6 meses de 2026 frente a 6 meses de 2025
OCF = [
    flow("2025-01-01", "2025-12-31", 1000.0),
    flow("2026-01-01", "2026-06-30", 600.0, "10-Q"),
    flow("2025-01-01", "2025-06-30", 400.0, "10-Q"),
]
CAPEX = [
    flow("2025-01-01", "2025-12-31", 200.0),
    flow("2026-01-01", "2026-06-30", 150.0, "10-Q"),
    flow("2025-01-01", "2025-06-30", 100.0, "10-Q"),
]


def test_ttm_adds_the_current_ytd_and_subtracts_the_same_period_of_last_year():
    assert flow_ttm(OCF) == (1200.0, "2026-06-30")                    # 1000 + 600 − 400
    assert flow_ttm(CAPEX) == (250.0, "2026-06-30")


def test_ttm_is_the_fiscal_year_when_no_later_quarter_exists():
    assert flow_ttm(OCF[:1]) == (1000.0, "2025-12-31")


def test_ttm_falls_back_to_the_fiscal_year_without_a_comparable_prior_ytd():
    assert flow_ttm(OCF[:2]) == (1000.0, "2025-12-31")                # hay trimestre pero no su comparable: no se inventa


def test_ttm_needs_a_fiscal_year_and_ignores_duplicates_keeping_the_latest_filing():
    assert flow_ttm([flow("2026-01-01", "2026-06-30", 600.0, "10-Q")]) is None
    amended = [flow("2025-01-01", "2025-12-31", 900.0, filed="2026-02-01"),
               flow("2025-01-01", "2025-12-31", 1000.0, "10-K/A", filed="2026-03-01")]
    assert flow_ttm(amended) == (1000.0, "2025-12-31")


def test_full_us_company():
    data = gaap(
        NetCashProvidedByUsedInOperatingActivities=OCF, PaymentsToAcquirePropertyPlantAndEquipment=CAPEX,
        Liabilities=[inst("2026-03-31", 9999.0), inst("2026-06-30", 600.0)],
        StockholdersEquity=[inst("2026-03-31", 1.0), inst("2026-06-30", 300.0)],
    )
    assert core(parse_company_facts(data, TODAY)) == (2.0, 950.0, date(2026, 6, 30))   # 600/300; 1200 − 250


def test_missing_liabilities_are_derived_from_total_and_equity():
    """KO, UAL y ADM no publican «Liabilities»: total pasivo+patrimonio menos patrimonio."""
    data = gaap(
        LiabilitiesAndStockholdersEquity=[inst("2026-06-30", 1000.0)],
        StockholdersEquity=[inst("2026-06-30", 250.0)],
    )
    assert parse_company_facts(data, TODAY).liabilities_to_equity == pytest.approx(3.0)


def test_equity_including_minority_is_preferred():
    data = gaap(
        Liabilities=[inst("2026-06-30", 600.0)],
        StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest=[inst("2026-06-30", 400.0)],
        StockholdersEquity=[inst("2026-06-30", 300.0)],
    )
    assert parse_company_facts(data, TODAY).liabilities_to_equity == pytest.approx(1.5)


def test_negative_equity_gives_no_ratio_but_keeps_the_cash_flow():
    data = gaap(
        Liabilities=[inst("2026-06-30", 600.0)], StockholdersEquity=[inst("2026-06-30", -50.0)],
        NetCashProvidedByUsedInOperatingActivities=OCF, PaymentsToAcquirePropertyPlantAndEquipment=CAPEX,
    )
    assert core(parse_company_facts(data, TODAY)) == (None, 950.0, date(2026, 6, 30))


def test_old_data_is_ignored():
    """CB y PBR traían etiquetas abandonadas de 2019 y 2010: no son datos actuales."""
    old = [flow("2018-07-01", "2019-06-30", 1000.0)]
    data = gaap(NetCashProvidedByUsedInOperatingActivities=old, PaymentsToAcquirePropertyPlantAndEquipment=old,
                Liabilities=[inst("2019-06-30", 5.0)], StockholdersEquity=[inst("2019-06-30", 1.0)])
    assert parse_company_facts(data, TODAY) is None


def test_cash_flow_without_capex_is_not_invented():
    """Bancos y aseguradoras no tienen etiqueta de inversión en inmovilizado: sin dato, no «FCF = flujo operativo»."""
    data = gaap(NetCashProvidedByUsedInOperatingActivities=OCF)
    assert parse_company_facts(data, TODAY) is None


def test_only_dollars_count():
    data = {"facts": {"us-gaap": {"Liabilities": {"units": {"EUR": [inst("2026-06-30", 600.0)]}},
                                  "StockholdersEquity": {"units": {"EUR": [inst("2026-06-30", 300.0)]}}}}}
    assert parse_company_facts(data, TODAY) is None


def test_ifrs_filer_uses_the_fiscal_year_and_the_sign_of_capex_does_not_matter():
    ifrs = {"facts": {"ifrs-full": {
        "Equity": {"units": {"USD": [inst("2026-03-31", 500.0)]}},
        "Liabilities": {"units": {"USD": [inst("2026-03-31", 1000.0)]}},
        "CashFlowsFromUsedInOperatingActivities": {"units": {"USD": [flow("2025-04-01", "2026-03-31", 800.0, "20-F")]}},
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": {"units": {"USD": [flow("2025-04-01", "2026-03-31", -300.0, "20-F")]}},
    }}}
    assert core(parse_company_facts(ifrs, TODAY)) == (2.0, 500.0, date(2026, 3, 31))


def test_us_gaap_wins_over_ifrs_and_empty_documents_give_none():
    assert parse_company_facts({"facts": {}}, TODAY) is None
    assert parse_company_facts({}, TODAY) is None


# ---- proveedor -------------------------------------------------------------------------------------------
TICKERS = {"0": {"cik_str": 320193, "ticker": "AAPL"}, "1": {"cik_str": 1234, "ticker": "BRK-B"},
           "2": {"cik_str": 99, "ticker": "NODATA"}}
GOOD = gaap(Liabilities=[inst("2026-06-30", 600.0)], StockholdersEquity=[inst("2026-06-30", 300.0)])


def provider(responses, calls=None, **kw):
    async def fetch(url):
        if calls is not None:
            calls.append(url)
        r = responses(url) if callable(responses) else responses[url]
        if isinstance(r, Exception):
            raise r
        return r

    return EdgarFinancialsProvider("Prueba prueba@ejemplo.com", requests_per_second=1000, fetch_json=fetch,
                                   today=lambda: TODAY, **kw)


def facts_url(cik):
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


async def test_provider_maps_tickers_to_ciks_and_parses_each_company():
    responses = {"https://www.sec.gov/files/company_tickers.json": TICKERS, facts_url(320193): GOOD,
                 facts_url(1234): GOOD, facts_url(99): None}
    out = await provider(responses).get_financials(["AAPL", "BRK.B", "NODATA", "PBR-A"])
    assert out["AAPL"].liabilities_to_equity == pytest.approx(2.0)
    assert out["BRK.B"].liabilities_to_equity == pytest.approx(2.0)      # «BRK.B» de la app es «BRK-B» en la SEC
    assert out["NODATA"] == Financials() and out["PBR-A"] == Financials()    # 404 y sin CIK: se anota que se intentó


async def test_provider_downloads_the_ticker_map_once_and_never_asks_for_unknown_companies():
    calls = []
    responses = {"https://www.sec.gov/files/company_tickers.json": TICKERS, facts_url(320193): GOOD}
    p = provider(responses, calls)
    await p.get_financials(["AAPL", "ZZZZ"])
    await p.get_financials(["AAPL"])
    assert calls.count("https://www.sec.gov/files/company_tickers.json") == 1
    assert facts_url(320193) in calls and len(calls) == 3                  # ZZZZ no tiene CIK: ninguna petición


async def test_a_failure_in_one_company_does_not_stop_the_others():
    def responses(url):
        if url.endswith("company_tickers.json"):
            return TICKERS
        return RuntimeError("fallo de red") if url == facts_url(320193) else GOOD
    out = await provider(responses).get_financials(["AAPL", "BRK-B"])
    assert "AAPL" not in out and out["BRK-B"].liabilities_to_equity == pytest.approx(2.0)   # AAPL se reintenta otro día


async def test_being_blocked_by_the_sec_stops_the_run():
    calls = []

    def responses(url):
        if url.endswith("company_tickers.json"):
            return TICKERS
        return FinancialsError("SEC EDGAR respondió 403")
    with pytest.raises(FinancialsError):
        await provider(responses, calls).get_financials(["AAPL", "BRK-B", "NODATA"])


async def test_missing_ticker_list_is_a_provider_error():
    with pytest.raises(FinancialsError):
        await provider({"https://www.sec.gov/files/company_tickers.json": None}).get_financials(["AAPL"])


def test_the_user_agent_carries_the_configured_contact():
    assert provider({})._user_agent == "ScannerOpcionesApp Prueba prueba@ejemplo.com"


def test_the_freshest_tag_wins_over_the_first_one_with_data():
    """MU y LITE conservan una etiqueta de patrimonio con datos de 2021 y 2015 y usan otra ahora."""
    data = gaap(
        StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest=[inst("2021-09-02", 999.0)],
        StockholdersEquity=[inst("2026-05-28", 300.0)],
        Liabilities=[inst("2026-05-28", 600.0)],
    )
    assert parse_company_facts(data, TODAY).liabilities_to_equity == pytest.approx(2.0)


def test_the_freshest_flow_tag_wins_too():
    old_ocf = [flow("2018-01-01", "2018-12-31", 5.0)]
    data = gaap(
        NetCashProvidedByUsedInOperatingActivities=old_ocf,
        NetCashProvidedByUsedInOperatingActivitiesContinuingOperations=OCF,
        PaymentsToAcquireProductiveAssets=CAPEX,
    )
    assert parse_company_facts(data, TODAY).fcf_ttm == 950.0


def test_the_user_agent_is_ascii_even_if_the_contact_has_accents():
    ua = EdgarFinancialsProvider("Miguel Ángel Ruiz correo@ejemplo.com", fetch_json=lambda url: None)._user_agent
    assert ua == "ScannerOpcionesApp Miguel Angel Ruiz correo@ejemplo.com" and ua.isascii()
    import httpx
    httpx.Headers({"User-Agent": ua}).raw        # httpx no lanza UnicodeEncodeError
