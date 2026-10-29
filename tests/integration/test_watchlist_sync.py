"""La Watchlist sigue a las fuentes del Universo con opciones: resumen previo la primera vez, luego automática."""
import json

import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace

from scanner_opciones.app.service import AppService
from scanner_opciones.app.watchlist_sync import plan
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database

NOW = datetime(2026, 10, 26, 12, 0)


def make(tmp_path, sources, watchlist=(), grace=0, clock=None):
    gw = FakeGateway()      # sin conectar: add_watchlist no lanza la actualización diaria
    svc = AppService(gw, Database(tmp_path / "app.db"), Settings.model_validate({"watchlist_sync": {"grace_days": grace}}), clock or (lambda: NOW))
    svc.manual_sources.update(sources)
    svc.watchlist.add(list(watchlist), NOW)
    return svc


def test_plan_compares_clean_tickers_and_protects_positions():
    p = plan({"AAPL", "BRK", "KO"}, ["BRK-B", "MSFT", "IBM"], protected={"IBM"})
    assert p.add == ("AAPL", "KO") and p.remove == ("MSFT",) and p.protected == ("IBM",) and p.kept == 1


def test_plan_with_no_sources_proposes_nothing():
    assert plan(set(), ["AAPL"]).empty


async def test_nothing_changes_until_the_user_decides(tmp_path):
    svc = make(tmp_path, {"A": ["KO", "PG"]}, watchlist=["MSFT"])
    assert svc.watchlist_sync_mode() == ""
    p = svc.watchlist_sync_plan()
    assert p.add == ("KO", "PG") and p.remove == ("MSFT",)
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert svc.watchlist.list() == ["MSFT"]


async def test_activating_applies_the_plan_and_keeps_following_the_sources(tmp_path):
    svc = make(tmp_path, {"A": ["KO", "PG"]}, watchlist=["MSFT"])
    svc.state.positions = [SimpleNamespace(ticker="MSFT", option=None)]
    done = await svc.set_watchlist_sync(True)
    await svc.wait_idle()
    assert done.add == ("KO", "PG") and done.remove == () and done.protected == ("MSFT",)
    assert sorted(svc.watchlist.list()) == ["KO", "MSFT", "PG"]
    svc.remove_source_tickers("A", ["PG"])
    await svc.wait_idle()
    assert sorted(svc.watchlist.list()) == ["KO", "MSFT"]


async def test_instruments_are_never_removed(tmp_path):
    svc = make(tmp_path, {"A": ["KO"]}, watchlist=["ESTX50"])
    await svc.set_watchlist_sync(True)
    assert sorted(svc.watchlist.list()) == ["ESTX50", "KO"]


async def test_choosing_manual_leaves_the_watchlist_alone(tmp_path):
    svc = make(tmp_path, {"A": ["KO"]}, watchlist=["MSFT"])
    await svc.set_watchlist_sync(False)
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert svc.watchlist.list() == ["MSFT"] and svc.watchlist_sync_mode() == "off"


async def test_an_automatic_pass_that_removes_too_much_waits_for_confirmation(tmp_path):
    old = [f"T{i}" for i in range(60)]
    svc = make(tmp_path, {"A": ["KO"]}, watchlist=old)
    svc.meta.set("watchlist_sync", "on")
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert len(svc.watchlist.list()) == 60 and svc.wl_sync_held is not None and len(svc.wl_sync_held.remove) == 60
    await svc.set_watchlist_sync(True)
    assert svc.watchlist.list() == ["KO"] and svc.wl_sync_held is None


async def test_a_ticker_without_source_is_only_removed_after_the_grace_days(tmp_path):
    now = [NOW]
    svc = make(tmp_path, {"A": ["KO", "PG"]}, watchlist=["KO", "PG"], grace=7, clock=lambda: now[0])
    await svc.set_watchlist_sync(True)
    svc.remove_source_tickers("A", ["PG"])                     # el radar lo saca hoy...
    await svc.wait_idle()
    assert sorted(svc.watchlist.list()) == ["KO", "PG"]
    now[0] = NOW + timedelta(days=3)
    svc.manual_sources["A"] = ["KO", "PG"]                     # ...y a los 3 días vuelve: no se toca y se olvida que faltó
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert json.loads(svc.meta.get("watchlist_sync_absent")) == {}
    svc.manual_sources["A"] = ["KO"]
    now[0] = NOW + timedelta(days=4)
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert "PG" in svc.watchlist.list()                        # la cuenta empieza de nuevo
    now[0] = NOW + timedelta(days=11)
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert svc.watchlist.list() == ["KO"]


@pytest.mark.instruments
async def test_own_instruments_are_added_to_the_watchlist_even_without_a_source(tmp_path):
    svc = make(tmp_path, {"A": ["KO"]}, watchlist=["KO"])
    assert svc.watchlist_sync_mode() == "" and "ESTX50" not in svc.watchlist.list()
    svc.sync_watchlist_in_background()
    await svc.wait_idle()
    assert "ESTX50" in svc.watchlist.list()


async def test_ptp_never_enter_the_watchlist_and_leave_it_without_grace(tmp_path):
    svc = make(tmp_path, {"A": ["KO", "EPD", "ET"]}, watchlist=["MPLX"], grace=7)
    assert svc.watchlist_sync_plan().add == ("KO",)
    await svc.set_watchlist_sync(True)
    await svc.wait_idle()
    assert svc.watchlist.list() == ["KO"]       # MPLX sale sin esperar los días de gracia
    await svc.add_watchlist(SimpleNamespace(tickers=["PAA", "AAPL"]))
    assert sorted(svc.watchlist.list()) == ["AAPL", "KO"]
