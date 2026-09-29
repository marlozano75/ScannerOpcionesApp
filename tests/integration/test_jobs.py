from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import BrokerDisconnectedError
from scanner_opciones.domain.models import OptionChain, OptionContract, OptionQuote
from scanner_opciones.jobs.daily_update import DailyUpdater
from scanner_opciones.jobs.refresh import RefreshJob
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    ContractRepo, IVHistoryRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)

NOW = datetime(2026, 9, 29, 10, 0)
TODAY = NOW.date()


class Env:
    def __init__(self):
        self.db = Database(":memory:")
        self.gw = FakeGateway(connected=True)
        self.watch = WatchlistRepo(self.db)
        self.info = TickerInfoRepo(self.db)
        self.iv = IVHistoryRepo(self.db)
        self.contracts = ContractRepo(self.db)
        self.snaps = SnapshotRepo(self.db)
        self.settings = Settings()
        self.clock = NOW
        self.daily = DailyUpdater(
            self.gw, self.watch, self.info, self.iv, self.contracts, self.settings, lambda: self.clock
        )
        self.refresh = RefreshJob(
            self.gw, self.contracts, self.snaps, self.info, self.settings, lambda: self.clock, self.iv
        )

    def add_aapl(self):
        self.gw.sectors["AAPL"] = ("Technology", "Consumer Electronics")
        self.gw.prices["AAPL"] = 100.0
        self.gw.chains["AAPL"] = OptionChain(
            "AAPL", [TODAY + timedelta(days=30)], [50.0, 75.0, 80.0, 90.0]
        )
        self.gw.ex_dividend_days["AAPL"] = 12
        self.gw.iv_history["AAPL"] = [
            (TODAY - timedelta(days=n), 0.20 + 0.01 * (10 - n), 0.20 + 0.01 * (10 - n), 0.20 + 0.01 * (10 - n))
            for n in range(10, 0, -1)
        ]
        self.watch.add(["AAPL"], NOW)


@pytest.fixture
def env():
    return Env()


async def test_daily_update_populates_everything(env):
    env.add_aapl()
    report = await env.daily.run_pending()
    assert report.updated == ["AAPL"] and report.errors == {}
    info = env.info.get("AAPL")
    assert info.sector == "Technology" and info.category == "Consumer Electronics"
    assert info.underlying_price == 100.0 and info.days_to_ex_dividend == 12
    assert info.iv_rank == 100.0  # última IV = máxima de la serie
    assert info.iv_percentile == pytest.approx(90.0)
    assert [c.strike for c in env.contracts.list("AAPL")] == [75.0, 80.0]  # guardado 15-45 % a DTE 30 (50 y 90 quedan fuera)
    assert env.watch.pending_daily_update(TODAY) == []


async def test_iv_history_is_incremental(env):
    env.add_aapl()
    await env.daily.run(["AAPL"])
    env.gw.calls.clear()
    env.gw.iv_history["AAPL"].append((TODAY, 0.5, 0.5, 0.5))
    await env.daily.run(["AAPL"])
    assert env.gw.calls == [("get_iv_history", "AAPL", TODAY - timedelta(days=1))]
    assert len(env.iv.series("AAPL")) == 11


async def test_ticker_added_after_daily_run(env):
    env.add_aapl()
    await env.daily.run_pending()
    env.gw.prices["KO"] = 60.0
    env.gw.chains["KO"] = OptionChain("KO", [TODAY + timedelta(days=30)], [45.0, 48.0])
    env.watch.add(["KO"], NOW + timedelta(hours=3))
    report = await env.daily.run_pending()
    assert report.updated == ["KO"]  # AAPL no se repite


async def test_failure_in_one_ticker_does_not_stop_batch(env):
    env.add_aapl()
    env.gw.prices["KO"] = 60.0
    env.watch.add(["KO"], NOW)
    env.gw.failing_tickers.add("AAPL")
    report = await env.daily.run(["AAPL", "KO"])
    assert report.updated == ["KO"] and "AAPL" in report.errors
    assert env.watch.pending_daily_update(TODAY) == ["AAPL"]


async def test_disconnected_propagates(env):
    env.add_aapl()
    env.gw.connected = False
    with pytest.raises(BrokerDisconnectedError):
        await env.daily.run(["AAPL"])


async def _prepare_refresh(env):
    env.add_aapl()
    await env.daily.run_pending()
    c75, c80 = env.contracts.list("AAPL")
    env.gw.quotes[c75] = OptionQuote(bid=1.0, ask=1.2, last=1.1, delta=-0.1, iv=0.3, open_interest=300)
    env.gw.quotes[c80] = OptionQuote(bid=0.2, ask=0.3, open_interest=50)   # yield < 1% -> sin what-if
    env.gw.margins[c75] = 1500.0
    return c75, c80


async def test_refresh_computes_metrics_and_margin_only_for_qualifying(env):
    c75, c80 = await _prepare_refresh(env)
    report = await env.refresh.run()
    assert report.refreshed == 2 and report.margins_requested == 1
    by_strike = {s.contract.strike: s for s in env.snaps.all("AAPL")}
    s = by_strike[75.0]
    assert s.yield_pct == pytest.approx(1.1 / 75 * 100)
    assert s.yield_annualized_pct == pytest.approx(s.yield_pct * 365 / 30)
    assert s.spread_pct == pytest.approx(0.2 / 1.1 * 100)
    assert s.initial_margin == 1500.0 and s.updated_at == NOW
    assert s.iv_rank == 100.0
    assert by_strike[80.0].initial_margin is None


async def test_refresh_updates_timestamp(env):
    await _prepare_refresh(env)
    await env.refresh.run()
    env.clock = NOW + timedelta(minutes=5)
    await env.refresh.run()
    assert {s.updated_at for s in env.snaps.all()} == {NOW + timedelta(minutes=5)}


async def test_refresh_counts_contracts_without_quote(env):
    c75, c80 = await _prepare_refresh(env)
    del env.gw.quotes[c80]
    report = await env.refresh.run()
    assert report.refreshed == 1 and report.without_quote == 1


async def test_refresh_batches_and_isolates_errors(env):
    env.settings = Settings.model_validate({"refresh": {"batch_size": 1}})
    env.refresh.settings = env.settings
    c75, c80 = await _prepare_refresh(env)
    env.gw.failing_tickers.add("AAPL")
    report = await env.refresh.run()
    assert report.refreshed == 0 and "AAPL" in report.errors


async def test_daily_update_drops_contracts_that_do_not_exist(env):
    env.add_aapl()
    bad = OptionContract("AAPL", TODAY + timedelta(days=30), 75.0)
    env.gw.invalid_contracts.add(bad)   # strike de la cadena que no existe para ese vencimiento
    await env.daily.run(["AAPL"])
    assert [c.strike for c in env.contracts.list("AAPL")] == [80.0]


async def test_stored_range_is_15_to_45_pct_and_up_to_60_dte(env):
    env.gw.prices["AAPL"] = 100.0
    env.gw.chains["AAPL"] = OptionChain(
        "AAPL",
        [TODAY + timedelta(days=n) for n in (0, 1, 30, 60, 61)],
        [50.0, 55.0, 70.0, 85.0, 86.0],
    )
    env.watch.add(["AAPL"], NOW)
    await env.daily.run(["AAPL"])
    stored = {(c.expiry - TODAY).days: sorted(x.strike for x in env.contracts.list("AAPL") if x.expiry == c.expiry)
              for c in env.contracts.list("AAPL")}
    assert set(stored) == {1, 30, 60}            # DTE 0 y 61 fuera
    assert stored[30] == [55.0, 70.0, 85.0]      # -45 %, -30 %, -15 %; 50 (-50 %) y 86 (-14 %) fuera


async def test_refresh_only_quotes_contracts_in_scope_unless_criteria_given(env):
    from scanner_opciones.domain.enums import OperationType
    from scanner_opciones.scanner.criteria import criteria_from_settings
    env.gw.prices["AAPL"] = 100.0
    env.gw.chains["AAPL"] = OptionChain(
        "AAPL", [TODAY + timedelta(days=30), TODAY + timedelta(days=50)], [70.0]
    )
    env.watch.add(["AAPL"], NOW)
    await env.daily.run(["AAPL"])
    near, far = env.contracts.list("AAPL")
    for c in (near, far):
        env.gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=100)
    report = await env.refresh.run()                      # valores iniciales: Regular 25-35 DTE
    assert (report.stored, report.in_scope, report.refreshed) == (2, 1, 1)
    wide = criteria_from_settings(env.settings, OperationType.REGULAR).with_filters(dte_max=60)
    report = await env.refresh.run([wide])                # rango ampliado desde el formulario
    assert (report.in_scope, report.refreshed) == (2, 2)


async def test_refresh_updates_underlying_price_each_cycle(env):
    c75, c80 = await _prepare_refresh(env)
    assert env.info.get("AAPL").underlying_price == 100.0
    env.gw.prices["AAPL"] = 96.0                      # la acción se mueve entre ciclos
    report = await env.refresh.run()
    assert report.prices_updated == 1
    info = env.info.get("AAPL")
    assert info.underlying_price == 96.0 and info.updated_daily_at == NOW   # no cuenta como actualización diaria
    # con el precio nuevo, el strike 80 pasa a estar a 16,7 % (ya no cumple 20 %): sale del alcance
    assert report.in_scope == 1


async def test_refresh_keeps_previous_price_when_no_live_price(env):
    await _prepare_refresh(env)
    del env.gw.prices["AAPL"]                         # sin precio disponible
    report = await env.refresh.run()
    assert report.prices_updated == 0 and env.info.get("AAPL").underlying_price == 100.0


async def test_refresh_survives_price_errors(env):
    await _prepare_refresh(env)

    async def boom(tickers):
        from scanner_opciones.domain.errors import DataUnavailableError
        raise DataUnavailableError("sin datos")
    env.gw.get_underlying_quotes = boom
    report = await env.refresh.run()
    assert report.prices_updated == 0 and report.refreshed == 2


async def test_refresh_stores_bid_size(env):
    c75, c80 = await _prepare_refresh(env)
    env.gw.quotes[c75] = OptionQuote(bid=1.0, ask=1.2, open_interest=300, bid_size=42)
    await env.refresh.run()
    by = {s.contract.strike: s for s in env.snaps.all("AAPL")}
    assert by[75.0].bid_size == 42 and by[80.0].bid_size is None


async def test_refresh_recomputes_iv_rank_and_percentile_with_live_iv(env):
    c75, c80 = await _prepare_refresh(env)
    assert env.info.get("AAPL").iv_rank == 100.0          # valor del día: última barra = máximo
    hist = [v for _, v in env.iv.series("AAPL")]           # 0.20 ... 0.29
    live = 0.245                                           # la IV en directo es menor que la última barra
    env.gw.underlying_ivs["AAPL"] = live
    report = await env.refresh.run()
    assert report.iv_updated == 1
    info = env.info.get("AAPL")
    lo, hi = min(hist), max(hist)
    assert info.iv_rank == pytest.approx((live - lo) / (hi - lo) * 100)
    assert info.iv_percentile == pytest.approx(sum(1 for v in hist if v < live) / len(hist) * 100)
    # y los snapshots usan los valores recalculados
    assert all(s.iv_rank == pytest.approx(info.iv_rank) for s in env.snaps.all("AAPL"))


async def test_refresh_keeps_iv_stats_when_no_live_iv(env):
    await _prepare_refresh(env)
    before = env.info.get("AAPL")
    report = await env.refresh.run()                       # el fake no tiene IV en directo
    assert report.iv_updated == 0
    assert env.info.get("AAPL").iv_rank == before.iv_rank


async def test_todays_iv_bar_is_redone_on_next_daily_update(env):
    env.add_aapl()
    env.gw.iv_history["AAPL"].append((TODAY, 0.5, 0.5, 0.5))          # barra parcial de hoy
    await env.daily.run(["AAPL"])
    assert env.iv.series("AAPL")[-1] == (TODAY, 0.5)
    env.gw.iv_history["AAPL"][-1] = (TODAY, 0.9, 0.9, 0.9)            # al cierre el valor cambia
    await env.daily.run(["AAPL"])
    series = env.iv.series("AAPL")
    assert series[-1] == (TODAY, 0.9) and len(series) == 11   # se sustituye, no se duplica


async def test_iv_rank_uses_daily_high_low_range(env):
    env.add_aapl()
    # cierres 0.30 .. 0.32 pero la barra del medio llegó a 0.60 y a 0.10 durante el día
    env.gw.iv_history["AAPL"] = [
        (TODAY - timedelta(days=3), 0.30, 0.31, 0.29),
        (TODAY - timedelta(days=2), 0.31, 0.60, 0.10),
        (TODAY - timedelta(days=1), 0.32, 0.33, 0.30),
    ]
    await env.daily.run(["AAPL"])
    info = env.info.get("AAPL")
    assert info.iv_rank == pytest.approx((0.32 - 0.10) / (0.60 - 0.10) * 100)   # 44 (con cierres saldría 100)


async def test_old_bars_without_high_low_trigger_one_full_download(env):
    env.add_aapl()
    await env.daily.run(["AAPL"])
    # simula datos guardados antes de la migración v3: se borran máx/mín
    env.db.conn.execute("UPDATE iv_history SET high = NULL, low = NULL")
    env.db.conn.commit()
    env.gw.calls.clear()
    await env.daily.run(["AAPL"])
    assert env.gw.calls == [("get_iv_history", "AAPL", None)]           # descarga completa (una vez)
    env.gw.calls.clear()
    await env.daily.run(["AAPL"])
    assert env.gw.calls[0][2] is not None                               # ya incremental
