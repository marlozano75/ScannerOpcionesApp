import asyncio
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.app.service import AppService, SelectedContract
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OperationType, OptionRight, TrafficLight
from scanner_opciones.domain.errors import BrokerDisconnectedError
from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, VixData,
)
from scanner_opciones.jobs.scheduler import PeriodicRunner
from scanner_opciones.storage.db import Database
from scanner_opciones.watchlist.parser import parse_text

NOW = datetime(2026, 9, 29, 10, 0)
TODAY = NOW.date()


def make_service(**gw_kwargs):
    gw = FakeGateway(
        account=AccountSummary("DU1", net_liquidation=100_000, excess_liquidity=45_000,
                               cushion_pct=45.0, look_ahead_excess=32_000, post_expiration_excess=28_000,
                               highest_severity=1),
        vix=VixData(18.5, [(TODAY - timedelta(days=i), 17.0 + i) for i in range(5, 0, -1)],
                    [(TODAY + timedelta(days=20), 19.0)]),
        **gw_kwargs,
    )
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW)
    return svc, gw


def seed_market(gw):
    gw.sectors["AAPL"] = ("Technology", "Electronics")
    gw.prices["AAPL"] = 100.0
    gw.chains["AAPL"] = OptionChain("AAPL", [TODAY + timedelta(days=30)], [75.0, 80.0])
    gw.iv_history["AAPL"] = [(TODAY - timedelta(days=n), 0.2 + 0.01 * n) for n in range(20, 0, -1)]


async def started_service():
    svc, gw = make_service()
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    await svc.start()
    for c in svc.contracts.list():
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
        gw.margins[c] = 1_500.0
    await svc.refresh_all()
    return svc, gw


async def test_start_runs_daily_then_refresh_and_fills_state():
    svc, gw = await started_service()
    s = svc.state
    assert s.connected and s.last_daily_report.updated == ["AAPL"]
    assert s.risk.current.light is TrafficLight.GREEN
    assert s.risk.look_ahead.light is TrafficLight.AMBER
    assert s.risk.post_expiration.light is TrafficLight.RED
    assert s.risk.severity.light is TrafficLight.AMBER and s.risk.severity.level == 1
    assert s.vix.current == 18.5 and len(s.vix.last_closes) == 5
    assert s.last_refresh == NOW and s.last_refresh_report.refreshed == 2


async def test_start_without_connection_does_not_crash():
    svc, gw = make_service()

    async def boom():
        raise BrokerDisconnectedError("TWS apagado")
    gw.connect = boom
    await svc.start()
    assert not svc.state.connected and "TWS apagado" in svc.state.errors["connection"]


async def test_run_on_startup_can_be_disabled():
    svc, gw = make_service()
    svc.settings = Settings.model_validate({"daily_update": {"run_on_startup": False}})
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    await svc.start()
    assert svc.state.last_daily_report is None


async def test_add_watchlist_triggers_daily_update_for_new_only():
    svc, gw = await started_service()
    gw.prices["KO"] = 60.0
    new = await svc.add_watchlist(parse_text("AAPL KO"))
    assert new == ["KO"]
    assert svc.state.last_daily_report.updated == ["KO"]


async def test_scan_returns_enriched_results():
    svc, gw = await started_service()
    out = svc.scan(svc.criteria(OperationType.REGULAR))
    assert sorted(r.snapshot.contract.strike for r in out.results) == [75.0, 80.0]
    assert all(r.impact.sector == "Technology" for r in out.results)
    strict = svc.scan(svc.criteria(OperationType.REGULAR, min_oi=1000))
    assert strict.results == [] and strict.rejected_count == 2


async def test_diversification_uses_positions():
    svc, gw = await started_service()
    gw.positions = [Position("AAPL", 100, 20_000, "Technology"),
                    Position("PUT", -1, 0, "Health",
                             OptionContract("JNJ", TODAY + timedelta(days=10), 150, OptionRight.PUT))]
    await svc.refresh_all()
    sectors, weeks = svc.diversification()
    assert sectors.weights_pct["Technology"] == pytest.approx(20_000 / 35_000 * 100)
    assert len(weeks) == 5 and sum(w.total for w in weeks) == 15_000


async def test_simulate_uses_snapshot_margin_and_reports_cushion():
    svc, gw = await started_service()
    strike = svc.contracts.list()[0].strike
    exp = svc.contracts.list()[0].expiry
    r = await svc.simulate([SelectedContract("AAPL", exp, strike, 2)])
    assert r.added_margin == 3_000 and r.added_nominal == strike * 100 * 2
    assert r.cushion_after.cushion_pct == pytest.approx(42.0)


async def test_simulate_unknown_contract():
    svc, gw = await started_service()
    with pytest.raises(ValueError):
        await svc.simulate([SelectedContract("AAPL", TODAY, 1.0)])


async def test_connection_loss_during_refresh_marks_disconnected():
    svc, gw = await started_service()
    gw.connected = False
    assert await svc.refresh_all() is False
    assert not svc.state.connected and "connection" in svc.state.errors


async def test_overlapping_refresh_is_skipped():
    svc, gw = await started_service()
    gate = asyncio.Event()
    original = gw.get_account_summary

    async def slow():
        await gate.wait()
        return await original()
    gw.get_account_summary = slow
    first = asyncio.create_task(svc.refresh_all())
    await asyncio.sleep(0)
    assert svc.busy
    assert await svc.refresh_all() is False  # omitido, no se apila
    gate.set()
    assert await first is True


async def test_periodic_runner_survives_failures():
    calls = []

    async def action():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("fallo puntual")

    async def fast_sleep(_):
        await asyncio.sleep(0)

    runner = PeriodicRunner(action, 300, fast_sleep)
    runner.start()
    for _ in range(10):
        await asyncio.sleep(0)
    await runner.stop()
    assert len(calls) >= 2


async def test_switch_gateway_resets_account_state():
    svc, gw = await started_service()
    assert svc.state.account is not None
    gw2 = FakeGateway(account=AccountSummary("DU2", net_liquidation=50_000, excess_liquidity=5_000, cushion_pct=10.0))
    await svc.switch_gateway(gw2)
    assert not gw.connected and gw2.connected
    assert svc.state.account.account_id == "DU2"
    assert svc.state.risk.current.light is TrafficLight.RED


async def _two_ticker_service():
    svc, gw = await started_service()          # AAPL con contratos y cotizaciones
    gw.prices["MU"] = 100.0
    gw.chains["MU"] = OptionChain("MU", [TODAY + timedelta(days=30)], [70.0, 75.0])
    gw.iv_history["MU"] = [(TODAY - timedelta(days=n), 0.3 + 0.01 * n) for n in range(5, 0, -1)]
    svc.watchlist.add(["MU"], NOW)
    await svc.run_daily(["MU"])
    for c in svc.contracts.list("MU"):
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=100)
    await svc.refresh_all()
    return svc, gw


async def test_removing_a_ticker_deletes_its_contracts_snapshots_and_info():
    svc, gw = await _two_ticker_service()
    assert svc.contracts.list("MU") and svc.snapshots.all("MU") and svc.ticker_info.get("MU")
    removed = svc.remove_ticker("MU")
    assert removed["contracts"] == 2 and removed["ticker_info"] == 1
    assert "MU" not in svc.watchlist.list()
    assert svc.contracts.list("MU") == [] and svc.snapshots.all("MU") == [] and svc.ticker_info.get("MU") is None
    assert svc.contracts.list("AAPL") and svc.snapshots.all("AAPL")      # el resto intacto
    assert svc.iv_history.series("MU")                                  # el historial de IV se conserva


async def test_cleanup_orphans_removes_data_of_tickers_not_in_watchlist():
    svc, gw = await _two_ticker_service()
    svc.watchlist.remove("MU")                # como si se hubiera quitado con la versión anterior
    assert svc.contracts.list("MU")           # quedan huérfanos
    removed = svc.cleanup_orphans()
    assert removed == {"contracts": 2, "ticker_info": 1}
    assert svc.contracts.list("MU") == [] and svc.contracts.list("AAPL")


async def test_orphans_are_purged_on_start_and_never_quoted():
    svc, gw = await _two_ticker_service()
    svc.watchlist.remove("MU")
    await svc.refresh_all()                   # el refresco limpia antes de cotizar
    assert svc.contracts.list("MU") == [] and gw.calls.count(("get_iv_history", "MU", None)) <= 1


async def test_manual_daily_update_waits_for_its_turn_instead_of_being_skipped():
    svc, gw = await started_service()
    gate = asyncio.Event()
    original = gw.get_account_summary

    async def slow():
        await gate.wait()
        return await original()
    gw.get_account_summary = slow
    running = asyncio.create_task(svc.refresh_all())     # el refresco ocupa el bloqueo
    await asyncio.sleep(0)
    assert svc.busy
    assert await svc.run_daily(["AAPL"]) is None         # automático: se omite
    svc.launch(svc.run_daily(["AAPL"], wait=True))       # manual: se pone en cola
    await asyncio.sleep(0)
    assert svc.state.activity                            # la interfaz puede mostrar que hay algo en curso
    gate.set()
    await running
    await svc.wait_idle()
    assert svc.state.last_daily_report.updated == ["AAPL"] and svc.state.activity is None

