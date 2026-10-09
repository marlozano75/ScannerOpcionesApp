"""Tickers que no sirven (IBKR no los reconoce, sin cadena de opciones): se sacan de la watchlist con un aviso que se cierra."""
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


async def test_service_drops_unsupported_tickers_from_the_watchlist_and_notifies_them():
    svc, gw = make_service()
    seed_market(gw)
    gw.unsupported_tickers.add("ANSS")
    svc.watchlist.add(["AAPL", "ANSS"], NOW)
    await svc.start()
    await svc.wait_idle()
    assert svc.watchlist.list() == ["AAPL"]                    # ANSS fuera, AAPL intacto
    assert svc.ticker_info.get("ANSS") is None and svc.contracts.list("ANSS") == []
    assert "no reconocido" in svc.excluded_notice["ANSS"]
    assert svc.state.last_daily_report.unsupported.keys() == {"ANSS"}


async def test_excluded_tickers_are_not_remembered_and_can_be_added_again():
    svc, gw = make_service()
    svc.meta.set("watchlist_excluded", '{"ANSS": {"reason": "x", "at": "2026-01-01"}}')   # lista permanente antigua
    from scanner_opciones.app.service import AppService
    from scanner_opciones.config.settings import Settings
    again = AppService(gw, svc.watchlist.db, Settings(), lambda: NOW, market=svc.market)
    assert again.excluded_notice == {} and not again.meta.get("watchlist_excluded")
    assert "ANSS" in await again.add_watchlist(parse_text("ANSS, AAPL"))


def test_the_notice_shows_on_every_page_until_it_is_closed():
    svc, gw = make_service()
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    with TestClient(app) as client:
        client.portal.call(svc.wait_idle)
        svc.excluded_notice["ANSS"] = "Ticker no reconocido por IBKR: ANSS"
        for page in ("/watchlist", "/"):
            text = client.get(page).text
            assert "Tickers quitados de la watchlist" in text and "Ticker no reconocido por IBKR: ANSS" in text
        assert client.post("/excluded/dismiss").status_code == 204
        assert svc.excluded_notice == {}
        assert "Tickers quitados de la watchlist" not in client.get("/watchlist").text
