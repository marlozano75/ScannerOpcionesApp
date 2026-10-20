"""Listas públicas de tastytrade como fuentes del Universo (Dividend Champions para puts; Hourly, Liquid... aptas para spreads)."""
from datetime import datetime

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database

NOW = datetime(2026, 10, 17, 12, 0)     # sábado


class FakeLists:
    def __init__(self, lists):
        self.lists, self.calls = lists, 0

    async def get_public_watchlist(self, name):
        self.calls += 1
        if name not in self.lists:
            raise RuntimeError("no existe")
        return self.lists[name]


def make(tmp_path, lists):
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), Settings(), lambda: NOW, watchlists=FakeLists(lists))
    for t in ("KO", "PG", "NVDA", "SPCX", "TQQQ"):
        svc._has_options[t] = True
    return svc


async def test_lists_fill_sources_and_only_the_spread_ones_are_spread_sources(tmp_path):
    svc = make(tmp_path, {"Dividend Champions": ["KO", "PG"], "tasty Hourly Top Equities": ["NVDA", "SPCX"], "Liquid Symbols": ["TQQQ", "NVDA"]})
    done = await svc.sync_tasty_lists()
    assert done == {"DividendChampions": 2, "TastyHourly": 2, "TastyLiquid": 2}      # las demás listas no existen en el fake: se saltan
    assert svc.manual_sources["DividendChampions"] == ["KO", "PG"]
    flagged = svc.spread_source_names()
    assert {"TastyHourly", "TastyLiquid", "TastyIVR"} <= flagged and "DividendChampions" not in flagged
    assert svc.spread_only_tickers() == {"NVDA", "SPCX", "TQQQ"}                      # KO y PG van por la calidad de la empresa
    assert "TastyHourly" in svc.spread_builtin_names() and not svc.is_renamable_source("TastyHourly")
    assert not svc.watchlist.list()                                                    # no añaden nada a la watchlist


async def test_a_list_is_only_dumped_again_when_its_content_changes(tmp_path):
    lists = {"Dividend Champions": ["KO", "PG"]}
    svc = make(tmp_path, lists)
    assert await svc.sync_tasty_lists() == {"DividendChampions": 2}
    assert await svc.sync_tasty_lists() == {}
    lists["Dividend Champions"] = ["PG", "KO"]                                          # mismo contenido, otro orden
    assert await svc.sync_tasty_lists() == {}
    lists["Dividend Champions"] = ["KO"]
    assert await svc.sync_tasty_lists() == {"DividendChampions": 1}
    assert svc.manual_sources["DividendChampions"] == ["KO"]


async def test_no_provider_does_nothing(tmp_path):
    svc = AppService(FakeGateway(), Database(tmp_path / "app.db"), Settings(), lambda: NOW)
    assert await svc.sync_tasty_lists() == {}
