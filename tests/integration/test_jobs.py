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
            "AAPL", [TODAY + timedelta(days=30)], [50.0, 75.0, 80.0, 96.0]
        )
        self.gw.ex_dividend_days["AAPL"] = 12
        self.gw.iv_history["AAPL"] = [
            (TODAY - timedelta(days=n), 0.20 + 0.01 * (10 - n), 0.20 + 0.01 * (10 - n), 0.20 + 0.01 * (10 - n))
            for n in range(10, 0, -1)
        ]
        self.watch.add(["AAPL"], NOW)


def _iv_calls(env):
    return [c for c in env.gw.calls if c[0] == "get_iv_history"]


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
    assert [c.strike for c in env.contracts.list("AAPL")] == [75.0, 80.0]  # guardado 5-40 % a DTE 30 (50 y 96 quedan fuera)
    assert env.watch.pending_daily_update(TODAY) == []


async def test_iv_history_is_incremental(env):
    env.add_aapl()
    await env.daily.run(["AAPL"])
    env.gw.calls.clear()
    env.gw.iv_history["AAPL"].append((TODAY, 0.5, 0.5, 0.5))
    await env.daily.run(["AAPL"])
    assert _iv_calls(env) == [("get_iv_history", "AAPL", TODAY - timedelta(days=1))]
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


async def test_stored_range_is_5_to_40_pct_and_up_to_45_dte(env):
    env.gw.prices["AAPL"] = 100.0
    env.gw.chains["AAPL"] = OptionChain(
        "AAPL",
        [TODAY + timedelta(days=n) for n in (0, 1, 30, 45, 46)],
        [50.0, 60.0, 70.0, 85.0, 96.0],
    )
    env.watch.add(["AAPL"], NOW)
    await env.daily.run(["AAPL"])
    stored = {(c.expiry - TODAY).days: sorted(x.strike for x in env.contracts.list("AAPL") if x.expiry == c.expiry)
              for c in env.contracts.list("AAPL")}
    assert set(stored) == {1, 30, 45}            # DTE 0 y 46 fuera
    assert stored[30] == [60.0, 70.0, 85.0]      # -40 %, -30 %, -15 %; 50 (-50 %) y 96 (-4 %) fuera


async def test_refresh_only_quotes_contracts_in_scope_unless_criteria_given(env):
    from scanner_opciones.scanner.criteria import criteria_from_settings
    env.gw.prices["AAPL"] = 100.0
    env.gw.chains["AAPL"] = OptionChain(
        "AAPL", [TODAY + timedelta(days=30), TODAY + timedelta(days=44)], [70.0]
    )
    env.watch.add(["AAPL"], NOW)
    await env.daily.run(["AAPL"])
    near, far = env.contracts.list("AAPL")
    for c in (near, far):
        env.gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=100)
    report = await env.refresh.run()                      # valores iniciales del filtro: DTE 1-35
    assert (report.stored, report.in_scope, report.refreshed) == (2, 1, 1)
    wide = criteria_from_settings(env.settings).with_filters(dte_max=45)
    report = await env.refresh.run([wide])                # rango ampliado desde el formulario
    assert (report.in_scope, report.refreshed) == (2, 2)


async def test_refresh_updates_underlying_price_each_cycle(env):
    c75, c80 = await _prepare_refresh(env)
    assert env.info.get("AAPL").underlying_price == 100.0
    env.gw.prices["AAPL"] = 88.0                      # la acción se mueve entre ciclos
    report = await env.refresh.run()
    assert report.prices_updated == 1
    info = env.info.get("AAPL")
    assert info.underlying_price == 88.0 and info.updated_daily_at == NOW   # no cuenta como actualización diaria
    # con el precio nuevo, el strike 80 pasa a estar a 9,1 % (ya no cumple el 10 % mínimo): sale del alcance
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
    assert _iv_calls(env) == [("get_iv_history", "AAPL", None)]           # descarga completa (una vez)
    env.gw.calls.clear()
    await env.daily.run(["AAPL"])
    assert _iv_calls(env)[0][2] is not None                               # ya incremental


async def test_daily_update_logs_one_summary_per_ticker(env, caplog):
    import logging
    env.add_aapl()
    env.gw.invalid_contracts.add(OptionContract("AAPL", TODAY + timedelta(days=30), 75.0))
    with caplog.at_level(logging.INFO, logger="scanner_opciones.jobs.daily_update"):
        await env.daily.run(["AAPL"])
    msgs = [r.getMessage() for r in caplog.records if "combinaciones" in r.getMessage()]
    assert msgs == ["AAPL: 1 de 2 combinaciones nuevas existen en IBKR (las demás no están listadas; es normal)"]


# ---- catálogo incremental, caché de margen y lotes ----------------------------------------------

class CountingGateway(FakeGateway):
    """Cuenta los contratos que se validan y las llamadas por ticker."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.qualified: list[OptionContract] = []

    async def qualify_contracts(self, contracts):
        self.qualified += list(contracts)
        return await super().qualify_contracts(contracts)


async def test_second_daily_update_only_validates_new_combinations(env):
    env.gw = CountingGateway(connected=True)
    env.daily.gateway = env.gw
    env.add_aapl()
    env.gw.invalid_contracts.add(OptionContract("AAPL", TODAY + timedelta(days=30), 75.0))
    await env.daily.run(["AAPL"])
    assert len(env.gw.qualified) == 2                       # 75 y 80 (50 y 90 fuera de la ventana)
    assert [c.strike for c in env.contracts.list("AAPL")] == [80.0]
    env.gw.qualified.clear()
    await env.daily.run(["AAPL"])
    assert env.gw.qualified == []                           # ni las guardadas ni la inexistente se repiten
    # una expiración nueva solo valida las combinaciones de esa expiración
    env.gw.chains["AAPL"] = OptionChain(
        "AAPL", [TODAY + timedelta(days=30), TODAY + timedelta(days=37)], [75.0, 80.0]
    )
    await env.daily.run(["AAPL"])
    assert {c.expiry for c in env.gw.qualified} == {TODAY + timedelta(days=37)}
    assert len(env.contracts.list("AAPL")) == 3


async def test_revalidate_retries_combinations_known_as_missing(env):
    env.gw = CountingGateway(connected=True)
    env.daily.gateway = env.gw
    env.add_aapl()
    bad = OptionContract("AAPL", TODAY + timedelta(days=30), 75.0)
    env.gw.invalid_contracts.add(bad)
    await env.daily.run(["AAPL"])
    env.gw.invalid_contracts.clear()                        # IBKR lista ahora ese strike
    await env.daily.run(["AAPL"])
    assert [c.strike for c in env.contracts.list("AAPL")] == [80.0]    # sin revalidar no se entera
    env.gw.qualified.clear()
    await env.daily.run(["AAPL"], revalidate=True)
    assert [c.strike for c in env.contracts.list("AAPL")] == [75.0, 80.0]
    assert [c.strike for c in env.gw.qualified] == [75.0]   # solo reintenta la que faltaba


async def test_daily_update_keeps_snapshots_of_contracts_that_stay(env):
    c75, c80 = await _prepare_refresh(env)
    await env.refresh.run()
    assert len(env.snaps.all("AAPL")) == 2
    await env.daily.run(["AAPL"])                           # antes borraba y recreaba todo
    assert len(env.snaps.all("AAPL")) == 2


async def test_contracts_leaving_the_window_are_removed_with_their_snapshots(env):
    c75, c80 = await _prepare_refresh(env)
    await env.refresh.run()
    env.gw.prices["AAPL"] = 84.0                            # 75 pasa a -10.7 % (dentro); 80 (-4.8 %) y 50 (-40.5 %) quedan fuera de 5-40 %
    await env.daily.run(["AAPL"])
    assert [c.strike for c in env.contracts.list("AAPL")] == [75.0]
    assert [s.contract.strike for s in env.snaps.all("AAPL")] == [75.0]


async def test_expired_misses_are_purged_and_removed_with_ticker(env):
    env.add_aapl()
    env.gw.invalid_contracts.add(OptionContract("AAPL", TODAY + timedelta(days=30), 75.0))
    await env.daily.run(["AAPL"])
    assert len(env.contracts.miss_keys("AAPL")) == 1
    env.contracts.purge_expired_misses(TODAY + timedelta(days=31))
    assert env.contracts.miss_keys("AAPL") == set()
    env.contracts.add_misses("AAPL", [OptionContract("AAPL", TODAY + timedelta(days=30), 75.0)])
    env.contracts.delete_for_ticker("AAPL")
    assert env.contracts.miss_keys("AAPL") == set()


async def test_known_sector_is_not_requested_again(env):
    env.add_aapl()
    calls = []
    real = env.gw.get_sector_info

    async def sector(t):
        calls.append(t)
        return await real(t)

    env.gw.get_sector_info = sector
    await env.daily.run(["AAPL"])
    await env.daily.run(["AAPL"])
    assert calls == ["AAPL"]


async def test_prices_and_dividends_are_requested_in_one_batch(env):
    env.add_aapl()
    env.gw.prices["KO"] = 60.0
    env.gw.chains["KO"] = OptionChain("KO", [TODAY + timedelta(days=30)], [50.0])
    env.watch.add(["KO"], NOW)
    batches, singles = [], []
    real_q, real_p = env.gw.get_underlying_quotes, env.gw.get_underlying_price

    async def quotes(tickers):
        batches.append(list(tickers))
        return await real_q(tickers)

    async def price(t):
        singles.append(t)
        return await real_p(t)

    env.gw.get_underlying_quotes, env.gw.get_underlying_price = quotes, price
    report = await env.daily.run(["AAPL", "KO"])
    assert batches == [["AAPL", "KO"]] and singles == []
    assert report.updated == ["AAPL", "KO"]
    assert ("get_days_to_ex_dividend_many", ("AAPL", "KO")) in env.gw.calls
    assert env.info.get("AAPL").days_to_ex_dividend == 12


async def test_daily_update_runs_tickers_concurrently_and_isolates_failures(env):
    import asyncio
    env.add_aapl()
    for t in ("KO", "PEP", "BAD"):
        env.gw.prices[t] = 60.0
        env.gw.chains[t] = OptionChain(t, [TODAY + timedelta(days=30)], [50.0])
    env.watch.add(["KO", "PEP", "BAD"], NOW)
    env.gw.failing_tickers.add("BAD")
    running, peak = 0, 0
    real = env.gw.get_option_chain

    async def chain(t):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return await real(t)

    env.gw.get_option_chain = chain
    progress = []
    report = await env.daily.run(["AAPL", "KO", "PEP", "BAD"], lambda i, n, t: progress.append((i, n)))
    assert peak > 1                                         # hay solapamiento
    assert report.updated == ["AAPL", "KO", "PEP"] and list(report.errors) == ["BAD"]
    assert [i for i, _ in progress] == [1, 2, 3, 4]


async def test_disconnection_aborts_the_whole_daily_run(env):
    env.add_aapl()
    env.gw.chains["KO"] = OptionChain("KO", [TODAY + timedelta(days=30)], [50.0])
    env.gw.prices["KO"] = 60.0
    env.watch.add(["KO"], NOW)
    real = env.gw.get_option_chain

    async def chain(t):
        if t == "KO":
            raise BrokerDisconnectedError("TWS cayó")
        return await real(t)

    env.gw.get_option_chain = chain
    with pytest.raises(BrokerDisconnectedError):
        await env.daily.run(["AAPL", "KO"])


async def test_recent_margin_is_reused_and_old_one_is_requested_again(env):
    c75, c80 = await _prepare_refresh(env)
    r1 = await env.refresh.run()
    assert (r1.margins_requested, r1.margins_reused) == (1, 0)
    env.clock = NOW + timedelta(minutes=5)
    env.gw.margins[c75] = 9999.0                            # cambia en el broker, pero aún no toca pedirlo
    r2 = await env.refresh.run()
    assert (r2.margins_requested, r2.margins_reused) == (0, 1)
    assert {s.contract.strike: s.initial_margin for s in env.snaps.all("AAPL")}[75.0] == 1500.0
    env.clock = NOW + timedelta(minutes=61)                 # supera margin_max_age_minutes (60)
    r3 = await env.refresh.run()
    assert (r3.margins_requested, r3.margins_reused) == (1, 0)
    assert {s.contract.strike: s.initial_margin for s in env.snaps.all("AAPL")}[75.0] == 9999.0


# ---- mercado cerrado: no se pisa la última cotización válida --------------------------------------

async def test_empty_quote_keeps_last_valid_prices_and_their_timestamp(env):
    c75, c80 = await _prepare_refresh(env)
    await env.refresh.run()                                  # con mercado abierto: bid/ask válidos
    env.clock = NOW + timedelta(hours=14)                    # mercado cerrado: llega todo vacío salvo el OI
    env.gw.quotes[c75] = OptionQuote(open_interest=310)
    report = await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    assert (s.bid, s.ask, s.last, s.delta, s.iv) == (1.0, 1.2, 1.1, -0.1, 0.3)
    assert s.updated_at == NOW                               # se ve que la cotización es antigua
    assert s.open_interest == 310                            # lo nuevo sí se actualiza
    assert s.spread_pct == pytest.approx(0.2 / 1.1 * 100)    # métricas recalculadas con los precios conservados
    assert report.quotes_kept >= 1


async def test_valid_new_quote_replaces_the_previous_one(env):
    c75, c80 = await _prepare_refresh(env)
    await env.refresh.run()
    env.clock = NOW + timedelta(minutes=5)
    env.gw.quotes[c75] = OptionQuote(bid=0.9, ask=1.0, open_interest=300)
    await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    assert (s.bid, s.ask) == (0.9, 1.0) and s.updated_at == NOW + timedelta(minutes=5)
    assert s.delta == -0.1                                   # sin griegas nuevas se conservan las anteriores


async def test_empty_quote_without_previous_data_stays_empty(env):
    c75, c80 = await _prepare_refresh(env)
    env.gw.quotes[c75] = OptionQuote(open_interest=300)      # primera cotización ya vacía
    await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    assert s.bid is None and s.ask is None and s.updated_at == NOW
