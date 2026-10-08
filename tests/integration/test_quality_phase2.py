"""Apalancamiento y flujo de caja (SEC EDGAR): almacenamiento, actualización, escaneo y formulario."""
from dataclasses import replace
from datetime import date, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import FinancialsError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.financials import FakeFinancials, Financials
from scanner_opciones.storage.db import Database
from tests.integration.test_jobs import Env, NOW as JOB_NOW
from tests.integration.test_quality_integration import FULL, make_client, scanned_service, strikes
from tests.integration.test_service import NOW, FixedMarket, make_service, seed_market


def test_quality_row_stores_the_financials_fields():
    env = Env()
    env.info.upsert(TickerInfo("AAPL", sector="Tech", underlying_price=100.0))
    env.daily.quality.save([replace(FULL, liabilities_to_equity=2.56, fcf_ttm=1.3e11,
                                    financials_end=date(2026, 6, 27), financials_at=NOW)])
    got = env.info.get("AAPL")
    assert (got.liabilities_to_equity, got.fcf_ttm, got.financials_end, got.financials_at) == (
        2.56, 1.3e11, date(2026, 6, 27), NOW)
    assert got.eps_ttm == 8.7 and got.next_earnings == date(2026, 10, 29) and got.sector == "Tech"


async def test_the_daily_update_does_not_erase_the_financials():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    env.daily.quality.save([TickerInfo("AAPL", liabilities_to_equity=1.1, fcf_ttm=5e9, financials_at=JOB_NOW)])
    env.watch.mark_daily_updated("AAPL", JOB_NOW - timedelta(days=2))
    await env.daily.run(["AAPL"])
    assert env.info.get("AAPL").fcf_ttm == 5e9 and env.info.get("AAPL").liabilities_to_equity == 1.1


def edgar(env, data=None, error=None):
    env.fin = FakeFinancials(data or {}, error)
    env.daily.financials = env.fin
    return env.daily


async def test_update_financials_fills_the_fields_and_waits_for_the_refresh_period():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    d = edgar(env, {"AAPL": Financials(1.8, 2e9, date(2026, 6, 30))})
    assert await d.update_financials(["AAPL"]) == 1
    got = env.info.get("AAPL")
    assert (got.liabilities_to_equity, got.fcf_ttm, got.financials_end, got.financials_at) == (
        1.8, 2e9, date(2026, 6, 30), JOB_NOW)
    assert got.sector == "Technology"
    assert await d.update_financials(["AAPL"]) == 0                           # reciente: no se vuelve a consultar
    env.clock = JOB_NOW + timedelta(days=14)                                  # edgar.refresh_days
    assert await d.update_financials(["AAPL"]) == 1
    assert len(env.fin.calls) == 2


async def test_tickers_edgar_does_not_know_are_noted_so_they_are_not_retried_every_start():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    d = edgar(env, {})                                                        # FakeFinancials devuelve Financials() vacío
    await d.update_financials(["AAPL"])
    got = env.info.get("AAPL")
    assert got.fcf_ttm is None and got.financials_at == JOB_NOW
    assert await d.update_financials(["AAPL"]) == 0


async def test_an_edgar_failure_keeps_the_stored_data_and_stops():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    env.daily.quality.save([TickerInfo("AAPL", liabilities_to_equity=1.1, fcf_ttm=5e9,
                                           financials_at=JOB_NOW - timedelta(days=30))])
    d = edgar(env, error=FinancialsError("SEC EDGAR respondió 403"))
    assert await d.update_financials(["AAPL"]) == 0
    assert env.info.get("AAPL").fcf_ttm == 5e9


async def test_financials_are_saved_in_batches_so_a_block_keeps_the_progress():
    env = Env()
    for t in ("AAA", "BBB", "CCC"):
        env.info.upsert(TickerInfo(t, underlying_price=10.0))

    class Flaky(FakeFinancials):
        async def get_financials(self, tickers):
            self.calls.append(tuple(tickers))
            if len(self.calls) == 2:
                raise FinancialsError("bloqueado")
            return {t: Financials(1.0, 1e6) for t in tickers}

    env.daily.financials = Flaky()
    assert await env.daily.update_financials(["AAA", "BBB", "CCC"], batch=2) == 2    # el 1.er lote se guardó; el 2.º falló
    assert env.info.get("AAA").fcf_ttm == 1e6 and env.info.get("CCC").financials_at is None


async def test_without_a_provider_nothing_happens_for_financials():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    assert await env.daily.update_financials(["AAPL"]) == 0


async def test_scanner_applies_the_financial_filters():
    svc, _ = await scanned_service(sector="Technology")
    svc.quality.save([TickerInfo("AAPL", liabilities_to_equity=3.5, fcf_ttm=-1e6)])
    assert strikes(svc) == [75.0, 80.0]
    assert strikes(svc, max_liabilities_to_equity=2.0) == []
    assert strikes(svc, require_positive_fcf=True) == []
    svc.quality.save([TickerInfo("AAPL", liabilities_to_equity=1.2, fcf_ttm=4e9)])
    assert strikes(svc, max_liabilities_to_equity=2.0, require_positive_fcf=True) == [75.0, 80.0]


async def test_service_start_launches_edgar_in_the_background():
    _, gw = make_service()
    seed_market(gw)
    fin = FakeFinancials({"AAPL": Financials(1.5, 3e9, date(2026, 6, 30))})
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True), financials=fin)
    svc.watchlist.add(["AAPL"], NOW)
    await svc.start()
    await svc.wait_idle()
    got = svc.ticker_info.get("AAPL")
    assert (got.liabilities_to_equity, got.fcf_ttm) == (1.5, 3e9)
    n = len(fin.calls)
    svc.update_financials_in_background()            # nada caduca: no vuelve a consultar
    await svc.wait_idle()
    assert len(fin.calls) == n


async def test_scanner_form_reads_the_financial_fields_and_shows_the_columns():
    svc, _ = await scanned_service(sector="Technology")
    svc.quality.save([TickerInfo("AAPL", liabilities_to_equity=3.5, fcf_ttm=-2.5e6)])
    base = "/scanner?submitted=1&discount=1&dte_min=1&dte_max=45&min_yield=0&ref=bid"
    with make_client(svc) as client:
        page = client.get(base).text
        assert "Apalancamiento y caja" in page and "Pasivo/Patr." in page and "FCF (M$)" in page and "3.5" in page
        assert "falta <code>edgar.contact</code>" in page                  # sin contacto configurado se avisa
        assert "Ningún contrato" in client.get(base + "&q_lev=2").text
        assert "Ningún contrato" in client.get(base + "&q_fcf=on").text
        assert "no permitida" in client.get(base + "&q_lev=7").text
        assert 'name="q_fcf" id="q_fcf" checked' in client.get(base + "&q_fcf=on&q_lev=5").text
