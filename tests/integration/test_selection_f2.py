"""Selección natural de tickers (F2): estados por recurrencia, cuarentena que saca de la Watchlist, exentos, liberación y templados."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.scanner.premium import PremiumRow
from scanner_opciones.storage.db import Database

NOW = datetime(2026, 11, 20, 12, 0)


def make(tmp_path, tickers=("GOOD", "BAD", "OPEN", "MEH"), selection=None):
    cfg = {"watchlist_sync": {"grace_days": 7}, "scanner": {"selection": {"enabled": True, **(selection or {})}}}
    clock = {"now": NOW}
    svc = AppService(FakeGateway(), Database(tmp_path / "app.db"), Settings.model_validate(cfg), lambda: clock["now"])
    svc.manual_sources.update({"Fuente": list(tickers)})
    svc.watchlist.add(list(tickers), NOW)
    svc.meta.set("watchlist_sync", "on")
    svc.state.positions = [SimpleNamespace(ticker="OPEN", option=None)]
    return svc, clock


def measure(svc, ticker, days_ago, best_closable):
    day = (NOW - timedelta(days=days_ago)).date()
    # perfil «Inicial»: yield mínimo 1 % (scanner.initial)
    svc.premium_repo.record_day(day, [PremiumRow(ticker, "Inicial", 3, 1 if best_closable else 0, best_closable, best_closable, None, 10.0, 300)], 365)


def fill(svc, ticker, values):
    for n, v in enumerate(values):
        measure(svc, ticker, len(values) - n, v)


async def test_nothing_is_decided_without_enough_sessions(tmp_path):
    svc, _ = make(tmp_path)
    fill(svc, "BAD", [0.0, 0.0, 0.0])                                    # solo 3 sesiones con medida (mínimo 5)
    svc.evaluate_selection()
    await svc.wait_idle()
    assert svc.ticker_selection()["BAD"]["state"] == "active" and "BAD" in svc.watchlist.list()


async def test_recurrence_decides_states_and_quarantine_leaves_the_watchlist(tmp_path):
    svc, clock = make(tmp_path)
    fill(svc, "GOOD", [1.5, 1.2, 0.3, 1.1, 1.0, 0.2])                   # 4 aciertos
    fill(svc, "BAD", [0.1, 0.0, 0.2, 0.1, 0.3, 0.1])                    # lejísimos
    fill(svc, "OPEN", [0.0] * 6)                                          # igual de malo, pero tiene posición
    fill(svc, "MEH", [0.7, 0.8, 0.9, 0.2, 0.1, 0.65])                   # casi
    counts = svc.evaluate_selection()
    await svc.wait_idle()
    sel = svc.ticker_selection()
    assert {t: sel[t]["state"] for t in sel} == {"GOOD": "active", "BAD": "quarantine", "OPEN": "active", "MEH": "warm"}
    assert counts == {"active": 2, "warm": 1, "quarantine": 1}
    assert "exento" in sel["OPEN"]["reason"]
    assert sorted(svc.watchlist.list()) == ["GOOD", "MEH", "OPEN"]       # la cuarentena sale ya, sin esperar la gracia de 7 días
    assert svc.warm_tickers() == {"MEH"} and svc.quarantined_tickers() == {"BAD"}
    # mientras dure, la sincronización no vuelve a meterlo aunque la fuente lo liste
    assert "BAD" not in svc.watchlist_sync_plan().add


async def test_quarantine_expires_and_only_new_measurements_count(tmp_path):
    svc, clock = make(tmp_path)
    fill(svc, "BAD", [0.1] * 6)
    svc.evaluate_selection()
    await svc.wait_idle()
    assert "BAD" not in svc.watchlist.list()
    clock["now"] = NOW + timedelta(days=14)
    svc.evaluate_selection()
    await svc.wait_idle()
    assert svc.ticker_selection()["BAD"]["state"] == "active" and svc.quarantined_tickers() == set()
    assert "BAD" in svc.watchlist.list()                                  # vuelve a la Watchlist
    svc.evaluate_selection()                                              # las medidas de antes de la liberación ya no cuentan
    assert svc.ticker_selection()["BAD"]["state"] == "active"


async def test_disabled_selection_does_nothing(tmp_path):
    svc, _ = make(tmp_path, selection={"enabled": False})
    fill(svc, "BAD", [0.1] * 6)
    assert svc.evaluate_selection() == {} and svc.ticker_selection() == {} and sorted(svc.watchlist.list()) == ["BAD", "GOOD", "MEH", "OPEN"]


async def test_source_profiles_restrict_which_profiles_count(tmp_path):
    svc, _ = make(tmp_path, selection={"source_profiles": {"Fuente": ["Estrategia"]}})
    fill(svc, "BAD", [2.0] * 6)                                           # cumple «Inicial», pero la fuente solo apunta a «Estrategia»
    for n in range(6):
        svc.premium_repo.record_day((NOW - timedelta(days=n + 1)).date(), [PremiumRow("BAD", "Estrategia", 3, 0, 0.1, None, None, 10.0, 300)], 365)
    svc.evaluate_selection()
    await svc.wait_idle()
    assert svc.ticker_selection()["BAD"]["state"] == "quarantine"
