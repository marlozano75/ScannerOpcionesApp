"""Datos de calidad: almacenamiento, actualización, escaneo y formulario."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.domain.models import OptionQuote, TickerInfo
from scanner_opciones.marketdata.fundamentals import FakeFundamentals, Fundamentals
from scanner_opciones.ui.web import create_app
from tests.integration.test_jobs import Env, NOW as JOB_NOW, TODAY as JOB_TODAY
from tests.integration.test_service import NOW, TODAY, make_service, seed_market

FULL = TickerInfo(
    "AAPL", underlying_price=100.0, eps_ttm=8.7, positive_quarters=4, reported_quarters=4, market_cap=4.8e12,
    option_liquidity=4, next_earnings=date(2026, 10, 29), eps_surprise_pct=-6.6, fundamentals_at=NOW,
)


def test_quality_is_stored_apart_and_joined_into_the_ticker_info():
    env = Env()
    env.info.upsert(TickerInfo("AAPL", sector="Tech", underlying_price=100.0, iv_rank=40.0))
    env.daily.quality.save([FULL])
    got = env.info.get("AAPL")                                  # la ficha se lee con los datos de calidad
    assert (got.sector, got.underlying_price, got.iv_rank) == ("Tech", 100.0, 40.0)
    assert (got.eps_ttm, got.positive_quarters, got.market_cap, got.next_earnings) == (8.7, 4, 4.8e12, date(2026, 10, 29))
    assert env.info.all()["AAPL"].fundamentals_at == NOW
    # una ficha sin datos de calidad los deja en None, sin romper nada
    env.info.upsert(TickerInfo("KO", underlying_price=60.0))
    assert env.info.get("KO").eps_ttm is None and env.info.get("KO").next_earnings is None


def test_quality_exists_for_tickers_that_are_not_in_the_watchlist():
    """Es lo que permite filtrar el Universo ANTES de añadir los tickers a la watchlist."""
    env = Env()
    env.daily.quality.save([TickerInfo("NEW", eps_ttm=1.5, positive_quarters=4, reported_quarters=4)])
    assert env.info.get("NEW") is None                           # sin ficha de watchlist...
    assert env.daily.quality.get("NEW").eps_ttm == 1.5           # ...pero con calidad
    assert set(env.daily.quality.all()) == {"NEW"}
    env.daily.quality.save([TickerInfo("NEW", eps_ttm=2.5)])     # guardar sustituye la fila
    assert env.daily.quality.get("NEW").eps_ttm == 2.5 and env.daily.quality.get("NEW").positive_quarters is None


def test_quality_purge_keeps_only_the_given_tickers():
    env = Env()
    env.daily.quality.save([TickerInfo("A", eps_ttm=1.0), TickerInfo("B", eps_ttm=2.0), TickerInfo("C", eps_ttm=3.0)])
    assert env.daily.quality.purge_except(["A", "C"]) == 1 and set(env.daily.quality.all()) == {"A", "C"}


async def test_the_daily_update_does_not_erase_the_quality_fields():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    env.daily.quality.save([FULL])
    env.watch.mark_daily_updated("AAPL", JOB_NOW - timedelta(days=2))      # vuelve a estar pendiente
    await env.daily.run(["AAPL"])
    assert env.info.get("AAPL").eps_ttm == 8.7 and env.info.get("AAPL").next_earnings == date(2026, 10, 29)


def updater(env, funds=None, quarters=None, error=None):
    env.fake = FakeFundamentals(funds or {}, quarters or {}, error)
    env.daily.fundamentals = env.fake
    return env.daily


async def test_update_fundamentals_fills_the_fields_and_fetches_the_quarters_once():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    d = updater(env, {"AAPL": Fundamentals(8.7, 4.8e12, 4, date(2026, 10, 29), -6.6)}, {"AAPL": [1.85, 2.85, 2.02, -0.5]})
    assert await d.update_fundamentals(["AAPL"]) == 1
    got = env.info.get("AAPL")
    assert (got.eps_ttm, got.market_cap, got.option_liquidity) == (8.7, 4.8e12, 4)
    assert (got.positive_quarters, got.reported_quarters) == (3, 4) and got.fundamentals_at == JOB_NOW
    assert got.sector == "Technology"                                   # el resto de la ficha intacto
    await d.update_fundamentals(["AAPL"])                               # reciente: no vuelve a pedir el historial
    assert [c for c in env.fake.calls if c[0] == "get_quarterly_eps"] == [("get_quarterly_eps", ("AAPL",))]


async def test_quarters_are_fetched_again_after_the_refresh_period_or_after_a_report():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    d = updater(env, {"AAPL": Fundamentals(8.7, 4.8e12, 4, JOB_TODAY + timedelta(days=5))}, {"AAPL": [1.0, 1.0, 1.0, 1.0]})
    await d.update_fundamentals(["AAPL"])
    env.clock = JOB_NOW + timedelta(days=8)                              # pasó el plazo de 7 días
    await d.update_fundamentals(["AAPL"])
    assert len([c for c in env.fake.calls if c[0] == "get_quarterly_eps"]) == 2
    env.clock = JOB_NOW + timedelta(days=8, hours=1)
    env.daily.quality.save([TickerInfo("AAPL", eps_ttm=8.7, positive_quarters=4, reported_quarters=4,
                                        next_earnings=JOB_TODAY + timedelta(days=5),
                                        fundamentals_at=env.clock - timedelta(days=1))])
    env.clock = env.clock + timedelta(days=10)                           # los resultados previstos ya han pasado
    await d.update_fundamentals(["AAPL"])
    assert len([c for c in env.fake.calls if c[0] == "get_quarterly_eps"]) == 3


async def test_eps_comes_from_the_quarters_when_the_provider_has_none():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    d = updater(env, {"AAPL": Fundamentals(None, 1e10, 3, None)}, {"AAPL": [1.9, 2.04, 2.26, 2.47]})
    await d.update_fundamentals(["AAPL"])
    assert env.info.get("AAPL").eps_ttm == pytest.approx(8.67)


async def test_a_provider_failure_keeps_the_stored_quality_data():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    env.daily.quality.save([FULL])
    d = updater(env, error=VolatilityError("caído"))
    assert await d.update_fundamentals(["AAPL"]) == 0
    assert env.info.get("AAPL").eps_ttm == 8.7


async def test_without_a_provider_nothing_happens():
    env = Env()
    env.add_aapl()
    assert await env.daily.update_fundamentals(["AAPL"]) == 0


# ---- escáner y formulario ---------------------------------------------------------------------------------
async def scanned_service(**info_fields):
    svc, gw = make_service()
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    await svc.start()
    await svc.wait_idle()
    for c in svc.contracts.list():
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
    await svc.refresh_all()
    svc.quality.save([TickerInfo("AAPL", **info_fields)])
    return svc, gw


def strikes(svc, **overrides):
    crit = svc.criteria().with_filters(strike_below_pct_min=1, strike_below_pct_max=40, min_annual_yield_pct=0,
                                       dte_min=1, dte_max=45, **overrides)
    return sorted(r.snapshot.contract.strike for r in svc.scan(crit).results)


async def test_scanner_applies_the_quality_filters():
    svc, _ = await scanned_service(eps_ttm=-0.4, positive_quarters=1, reported_quarters=4, market_cap=1e9)
    assert strikes(svc) == [75.0, 80.0]
    assert strikes(svc, require_profitable=True) == []
    assert strikes(svc, min_positive_quarters=3) == []
    assert strikes(svc, min_market_cap_m=2000.0) == []
    good = TickerInfo("AAPL", eps_ttm=5.0, positive_quarters=4, reported_quarters=4, market_cap=9e9, option_liquidity=3)
    svc.quality.save([good])
    assert strikes(svc, require_profitable=True, min_positive_quarters=3, min_market_cap_m=2000.0,
                   min_option_liquidity=3) == [75.0, 80.0]


async def test_earnings_filter_only_drops_the_contracts_that_cross_the_report():
    svc, _ = await scanned_service(next_earnings=TODAY + timedelta(days=10))      # los contratos del test vencen a 30 días
    assert strikes(svc) == [75.0, 80.0]
    assert strikes(svc, avoid_earnings=True) == []
    svc.quality.save([TickerInfo("AAPL", next_earnings=TODAY + timedelta(days=40))])
    assert strikes(svc, avoid_earnings=True) == [75.0, 80.0]                         # los resultados llegan tras el vencimiento


def make_client(svc):
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    return TestClient(app)


async def test_scanner_form_reads_the_quality_fields():
    svc, _ = await scanned_service(eps_ttm=-0.4, positive_quarters=1, reported_quarters=4, market_cap=1e9)
    base = "/scanner?submitted=1&discount=1&dte_min=1&dte_max=45&min_yield=0&ref=bid"
    with make_client(svc) as client:
        page = client.get(base).text
        assert "Calidad de la empresa" in page and "contratos cumplen" in page
        assert "Ningún contrato" not in page
        assert "Ningún contrato" in client.get(base + "&q_profit=on").text           # EPS negativo
        assert "Ningún contrato" in client.get(base + "&q_quarters=3").text
        assert "Ningún contrato" in client.get(base + "&q_mcap=2000").text
        assert "no permitida" in client.get(base + "&q_quarters=1").text              # fuera de las opciones configuradas
        assert "no permitida" in client.get(base + "&q_mcap=123").text
        checked = client.get(base + "&q_profit=on&q_earn=on&q_liq=3").text
        assert 'name="q_profit" id="q_profit" checked' in checked and 'name="q_earn" id="q_earn" checked' in checked


async def test_scanner_table_shows_the_quality_columns():
    svc, _ = await scanned_service(eps_ttm=5.0, positive_quarters=3, reported_quarters=4, market_cap=4.8e12,
                                   option_liquidity=4, next_earnings=TODAY + timedelta(days=10), eps_surprise_pct=-6.6)
    base = "/scanner?submitted=1&discount=1&dte_min=1&dte_max=45&min_yield=0&ref=bid"
    with make_client(svc) as client:
        page = client.get(base).text
    for header in ("EPS 12 m", "Trim. +", "Cap. (B$)", "Liq. opc.", "Resultados"):
        assert header in page
    assert "3/4" in page and str(TODAY + timedelta(days=10)) in page
    assert "Resultados antes del vencimiento" in page            # la fecha cae antes del vencimiento de los contratos: se marca


async def test_service_start_fills_the_quality_fields():
    from scanner_opciones.app.service import AppService
    from scanner_opciones.config.settings import Settings
    from scanner_opciones.storage.db import Database
    from tests.integration.test_service import FixedMarket

    _, gw = make_service()
    seed_market(gw)
    fake = FakeFundamentals({"AAPL": Fundamentals(5.0, 3e12, 4, TODAY + timedelta(days=9))}, {"AAPL": [1.0, 1.0, 1.0, -0.2]})
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True), fundamentals=fake)
    svc.watchlist.add(["AAPL"], NOW)
    await svc.start()
    await svc.wait_idle()
    got = svc.ticker_info.get("AAPL")
    assert (got.eps_ttm, got.positive_quarters, got.next_earnings) == (5.0, 3, TODAY + timedelta(days=9))
