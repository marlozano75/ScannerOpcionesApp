"""Tickers deslistados: no entran en el Universo desde las listas/ficheros y los que ya estaban se quitan; los que IBKR no reconoce no se
vuelven a ofrecer."""
from datetime import datetime, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.jobs.daily_update import DailyUpdateReport
from scanner_opciones.storage.db import Database
from scanner_opciones.watchlist.parser import parse_text

NOW = datetime(2026, 10, 21, 12, 0)


class FakeNames:
    def __init__(self, inactive=(), fail=False):
        self.inactive, self.fail, self.calls = set(inactive), fail, 0

    async def get_company_names(self, tickers):
        return {}

    async def get_inactive(self, tickers):
        self.calls += 1
        if self.fail:
            raise VolatilityError("tastytrade caído")
        return {t for t in tickers if t in self.inactive}


def make(tmp_path, names, clock=lambda: NOW):
    gw = FakeGateway()
    gw.connected = True
    return AppService(gw, Database(tmp_path / "app.db"), Settings(), clock, names=names)


def universe(svc):
    return {r.ticker for src in svc.universe_sources for r in src.table.rows}


async def test_a_delisted_ticker_does_not_enter_the_universe(tmp_path):
    svc = make(tmp_path, FakeNames({"FL"}))
    class Vol:      # solo IV Rank: tastytrade lo da también a los deslistados
        async def get_iv_metrics(self, tickers):
            return {t: object() for t in tickers}
    svc.volatility = Vol()
    res = await svc.add_manual_tickers(parse_text("AAPL FL"), "Lista")
    assert res["added"] == ["AAPL"] and res["no_options"] == ["FL"] and universe(svc) == {"AAPL"}


async def test_purge_removes_the_ones_already_stored_and_waits_a_week(tmp_path):
    names = FakeNames({"IPG", "LEG"})
    now = [NOW]
    svc = make(tmp_path, names, lambda: now[0])
    svc.manual_sources["Lista"] = ["AAPL", "IPG", "LEG"]
    assert await svc.purge_inactive() == 2 and universe(svc) == {"AAPL"} and svc._has_options == {"IPG": False, "LEG": False}
    assert await svc.purge_inactive() == 0 and names.calls == 1                      # no repite hasta pasada una semana
    now[0] = NOW + timedelta(days=8)
    svc.manual_sources["Lista"].append("LEG")
    assert await svc.purge_inactive() == 1 and names.calls == 2


async def test_a_failed_check_removes_nothing(tmp_path):
    svc = make(tmp_path, FakeNames({"IPG"}, fail=True))
    svc.manual_sources["Lista"] = ["IPG"]
    assert await svc.purge_inactive() == 0 and universe(svc) == {"IPG"}


def test_what_ibkr_does_not_recognise_is_not_offered_again_by_the_sources(tmp_path):
    svc = make(tmp_path, None)
    svc.manual_sources["Lista"] = ["WBD", "AAPL"]
    svc.watchlist.add(["WBD"], NOW)
    svc.exclude_unsupported(DailyUpdateReport(unsupported={"WBD": "Ticker no reconocido por IBKR: WBD"}))
    assert universe(svc) == {"AAPL"} and "WBD" not in svc.watchlist.list()
