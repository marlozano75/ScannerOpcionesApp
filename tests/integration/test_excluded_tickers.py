"""Tickers que no sirven (IBKR no los reconoce, sin cadena de opciones): se sacan de la watchlist y no vuelven."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.domain.errors import UnsupportedTickerError
from scanner_opciones.domain.models import OptionChain, OptionContract
from scanner_opciones.ui.web import create_app
from scanner_opciones.watchlist.parser import parse_text
from tests.integration.test_jobs import Env, NOW as JOB_NOW
from tests.integration.test_service import NOW, TODAY, make_service, seed_market


async def test_the_daily_update_reports_unsupported_tickers_apart_from_errors():
    env = Env()
    env.add_aapl()
    env.gw.unsupported_tickers.add("ANSS")
    env.gw.failing_tickers.add("FLAKY")                       # pasajero: error normal, no exclusión
    env.watch.add(["ANSS", "FLAKY"], JOB_NOW)
    report = await env.daily.run(["AAPL", "ANSS", "FLAKY"])
    assert report.updated == ["AAPL"]
    assert list(report.unsupported) == ["ANSS"] and "no reconocido" in report.unsupported["ANSS"]
    assert list(report.errors) == ["FLAKY"]


async def test_service_drops_unsupported_tickers_from_the_watchlist_and_remembers_them():
    svc, gw = make_service()
    seed_market(gw)
    gw.unsupported_tickers.add("ANSS")
    svc.watchlist.add(["AAPL", "ANSS"], NOW)
    await svc.start()
    await svc.wait_idle()
    assert svc.watchlist.list() == ["AAPL"]                    # ANSS fuera, AAPL intacto
    assert svc.ticker_info.get("ANSS") is None and svc.contracts.list("ANSS") == []
    assert "no reconocido" in svc.excluded["ANSS"]["reason"]
    assert svc.state.last_daily_report.unsupported.keys() == {"ANSS"}


async def test_excluded_tickers_survive_a_restart_and_cannot_be_added_again():
    svc, gw = make_service()
    seed_market(gw)
    gw.unsupported_tickers.add("ANSS")
    svc.watchlist.add(["AAPL", "ANSS"], NOW)
    await svc.start()
    await svc.wait_idle()
    # un servicio nuevo sobre la misma base de datos (reinicio)
    from scanner_opciones.app.service import AppService
    from scanner_opciones.config.settings import Settings
    again = AppService(gw, svc.watchlist.db, Settings(), lambda: NOW, market=svc.market)
    assert "ANSS" in again.excluded
    parsed = parse_text("ANSS, AAPL, KO")
    assert again.excluded_among(parsed) == ["ANSS"]
    assert await again.add_watchlist(parsed) == ["KO"]         # AAPL ya estaba; ANSS se ignora
    assert "ANSS" not in again.watchlist.list()


async def test_replace_watchlist_ignores_excluded_tickers_and_refuses_an_all_excluded_list():
    svc, gw = make_service()
    svc.excluded["ANSS"] = {"reason": "Ticker no reconocido por IBKR: ANSS", "at": NOW.isoformat()}
    svc.watchlist.add(["AAPL"], NOW)
    new, kept, removed = await svc.replace_watchlist(parse_text("AAPL, ANSS"))
    assert kept == ["AAPL"] and "ANSS" not in svc.watchlist.list()
    with pytest.raises(ValueError):
        await svc.replace_watchlist(parse_text("ANSS"))
    assert svc.watchlist.list() == ["AAPL"]                    # una lista inútil no vacía la watchlist


async def test_allow_ticker_makes_it_addable_again():
    svc, gw = make_service()
    svc.excluded["ANSS"] = {"reason": "x", "at": NOW.isoformat()}
    svc._save_excluded()
    assert svc.allow_ticker("ANSS") and not svc.allow_ticker("ANSS")
    assert svc.excluded == {} and svc._load_excluded() == {}


def test_watchlist_page_lists_excluded_tickers_and_allows_them_again():
    svc, gw = make_service()
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    svc.excluded["ANSS"] = {"reason": "Ticker no reconocido por IBKR: ANSS", "at": NOW.isoformat()}
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    with TestClient(app) as client:
        client.portal.call(svc.wait_idle)
        page = client.get("/watchlist").text
        assert "Excluidos automáticamente" in page and "ANSS" in page and "Permitir de nuevo" in page
        r = client.post("/watchlist/allow", data={"ticker": "anss"}, follow_redirects=False)
        assert r.status_code == 303 and "ANSS" not in svc.excluded
        assert "Excluidos automáticamente" not in client.get("/watchlist").text
