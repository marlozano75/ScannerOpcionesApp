"""Estrategia de venta de puts (fase 4): ATR con máximos y mínimos diarios y gestión de las puts vendidas."""
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import ManagementSettings, Settings, TrendSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, Position
from scanner_opciones.jobs.price_history import update_history
from scanner_opciones.marketdata.candles import FakeCandles
from scanner_opciones.metrics import technical as ta
from scanner_opciones.portfolio.management import manage_puts, put_status, ticker_weights
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import BarRepo

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_annual_yield_pct=0)
CFG = ManagementSettings()


# ---- ATR -----------------------------------------------------------------------------------------------------
def test_atr_is_the_wilder_average_of_the_true_range():
    flat = [(100.0, 101.0, 99.0)] * 20                              # rango diario 2, sin huecos
    assert ta.atr(flat, 14) == pytest.approx(2.0)
    gap = [(100.0, 101.0, 99.0), (110.0, 111.0, 109.0)]            # hueco al alza: TR = |111 − 100| = 11
    assert ta.atr(gap, 1) == pytest.approx(11.0)
    assert ta.atr(flat[:14], 14) is None                            # hacen falta n + 1 velas
    bars = [(100.0, 101.0, 99.0)] * 15 + [(100.0, 106.0, 98.0)]    # último TR = 8 → (2·13 + 8) / 14
    assert ta.atr(bars, 14) == pytest.approx((2 * 13 + 8) / 14)


def test_the_atr_filter_requires_the_strike_to_be_n_atr_away():
    c = OptionContract("AAPL", TODAY + timedelta(days=36), 90.0, OptionRight.PUT)
    snap = ContractSnapshot(c, NOW, bid=1.0, ask=1.1, delta=-0.2, iv=0.3, open_interest=500, spread_pct=5.0)
    f = BASE.with_filters(min_atr_multiple=3.0)
    assert reject_reason(snap, 100.0, TODAY, f, atr=3.0) is None                  # 10 ≥ 3 × 3
    assert "ATR" in reject_reason(snap, 100.0, TODAY, f, atr=4.0)                 # 10 < 12
    assert "sin ATR" in reject_reason(snap, 100.0, TODAY, f)
    assert reject_reason(snap, 100.0, TODAY, BASE) is None


# ---- máximos y mínimos en el histórico -----------------------------------------------------------------------
def bars(n, end=TODAY - timedelta(days=1), px=100.0):
    return [(end - timedelta(days=n - 1 - i), px + i * 0.1) for i in range(n)]


async def test_history_stores_highs_and_lows_and_backfills_old_rows_once():
    repo = BarRepo(Database(":memory:"))
    cfg = TrendSettings()
    series = bars(40)
    candles = FakeCandles(bars={"AAPL": series}, ranges={"AAPL": {d: (px + 1, px - 1) for d, px in series}})
    await update_history(candles, repo, ["AAPL"], TODAY, cfg)
    assert len(repo.recent_ohlc(["AAPL"], 100)["AAPL"]) == 40 and repo.missing_ranges(["AAPL"], 20, 15) == []
    assert repo.atr(["AAPL"], 14)["AAPL"] == pytest.approx(2.0)                   # rango diario de 2, sin huecos
    # histórico anterior a la migración v20: sin máximos ni mínimos
    old = BarRepo(Database(":memory:"))
    old.upsert("AAPL", series)
    assert old.missing_ranges(["AAPL"], 20, 15) == ["AAPL"] and old.atr(["AAPL"], 14) == {}
    candles2 = FakeCandles(bars={"AAPL": series}, ranges=candles.ranges)
    await update_history(candles2, old, ["AAPL"], TODAY, cfg)                       # al día en cierres, pero se rellenan los rangos
    assert candles2.days == [45] and old.atr(["AAPL"], 14)["AAPL"] == pytest.approx(2.0)
    await update_history(candles2, old, ["AAPL"], TODAY, cfg)
    assert candles2.days == [45]                                                      # y no se vuelve a pedir


def test_a_missing_high_does_not_erase_the_stored_one():
    repo = BarRepo(Database(":memory:"))
    d = TODAY - timedelta(days=1)
    repo.upsert_ohlc("AAPL", [(d, 100.0, 102.0, 98.0)])
    repo.upsert_ohlc("AAPL", [(d, 100.5, None, None)])
    assert repo.recent_ohlc(["AAPL"], 5)["AAPL"] == [(100.5, 102.0, 98.0)]


# ---- gestión de puts -----------------------------------------------------------------------------------------
def short_put(ticker="AAPL", strike=90.0, days=30, qty=-2, market_value=-220.0, avg_cost=110.0):
    """2 contratos vendidos a 1,10 $ (avg_cost 110 por contrato); ahora valen 1,10 $ (market_value −220)."""
    o = OptionContract(ticker, TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return Position(ticker, qty, market_value, "Technology", o, avg_cost)


def alerts(status):
    return {a.level: a.text for a in status.alerts}


def test_take_profit_at_50_percent_of_the_premium():
    s = put_status(short_put(market_value=-100.0), TODAY, 100.0, CFG)             # ahora 0,50 $ por acción: 55 % ganado
    assert s.entry_credit == pytest.approx(1.10) and s.current_price == pytest.approx(0.50) and s.captured_pct == pytest.approx(54.5, abs=0.1)
    assert "Recoger beneficios" in alerts(s)["ok"]
    quiet = put_status(short_put(), TODAY, 100.0, CFG)                            # sin ganancia y a 30 DTE: sin avisos
    assert quiet.captured_pct == pytest.approx(0.0) and quiet.alerts == ()


def test_review_at_21_dte_when_the_target_was_not_reached_and_not_when_it_was():
    s = put_status(short_put(days=20), TODAY, 100.0, CFG)
    assert "Revisar" in alerts(s)["warn"] and "rolarla" in alerts(s)["warn"]
    assert not any("Revisar" in a.text for a in put_status(short_put(days=20, market_value=-80.0), TODAY, 100.0, CFG).alerts)
    assert "sin dato de la prima" in alerts(put_status(short_put(days=10, avg_cost=None), TODAY, 100.0, CFG))["warn"]


def test_in_the_money_near_the_strike_and_losses():
    assert "En el dinero" in alerts(put_status(short_put(), TODAY, 89.0, CFG))["warn"]
    assert "menos de 2 %" in alerts(put_status(short_put(), TODAY, 91.0, CFG))["info"]
    loss = put_status(short_put(market_value=-440.0), TODAY, 100.0, CFG)
    assert loss.captured_pct == pytest.approx(-100.0) and "En pérdida" in alerts(loss)["info"]
    assert put_status(short_put(), TODAY, None, CFG).alerts == ()                 # sin precio del subyacente no se avisa


def test_weights_add_the_shares_you_own_and_what_you_would_buy_if_assigned():
    positions = [short_put("AAPL", 90.0, qty=-2), short_put("MSFT", 300.0, qty=-1), Position("AAPL", 100, 9_500.0, "Technology"),
                 Position("KO", 100, 6_000.0, "Staples")]
    weights = {w.ticker: w for w in ticker_weights(positions, 100_000.0, CFG)}
    assert set(weights) == {"AAPL", "MSFT"}                                      # solo acciones con puts vendidas
    aapl = weights["AAPL"]
    assert (aapl.stock_value, aapl.put_nominal, aapl.total, aapl.contracts) == (9_500.0, 18_000.0, 27_500.0, 2)
    assert aapl.pct_nlv == pytest.approx(27.5) and aapl.level == "warn"          # > 10 %
    assert weights["MSFT"].pct_nlv == pytest.approx(30.0)
    mid = ticker_weights([short_put("AAPL", 60.0, qty=-1)], 100_000.0, CFG)[0]   # 6 000 / 100 000 = 6 %
    assert mid.level == "info"
    assert ticker_weights([short_put("AAPL", 40.0, qty=-1)], 100_000.0, CFG)[0].level is None
    assert ticker_weights([short_put()], None, CFG)[0].pct_nlv is None


def test_the_report_orders_by_expiry_ignores_long_puts_and_counts_important_warnings():
    long_put = Position("AAPL", 1, 50.0, "Technology", OptionContract("AAPL", TODAY + timedelta(days=5), 80.0, OptionRight.PUT), 40.0)
    report = manage_puts([short_put("MSFT", 300.0, days=40), short_put("AAPL", days=10), long_put], 50_000.0, TODAY, {"AAPL": 100.0}, CFG)
    assert [(s.ticker, s.dte) for s in report.puts] == [("AAPL", 10), ("MSFT", 40)]
    assert report.attention >= 2                                                  # AAPL a 10 DTE y el peso de MSFT (60 %)
    assert manage_puts([], 50_000.0, TODAY, {}, CFG).puts == ()


def test_management_settings_are_validated():
    with pytest.raises(ValueError, match="warn_position_pct"):
        ManagementSettings(warn_position_pct=12, max_position_pct=10)
