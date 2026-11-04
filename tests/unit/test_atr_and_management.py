"""Estrategia de venta de puts (fase 4): ATR con máximos y mínimos diarios y gestión de las puts vendidas."""
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import ManagementSettings, Settings, TrendSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, Position
from scanner_opciones.jobs.price_history import update_history
from scanner_opciones.marketdata.candles import FakeCandles
from scanner_opciones.metrics import technical as ta
from scanner_opciones.portfolio.management import manage_puts, put_key, put_status, ticker_weights
from scanner_opciones.scanner.profiles import UNASSIGNED
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import BarRepo

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0)
CFG = ManagementSettings(default_profile="tastytrade")      # las pruebas de abajo comprueban las reglas de Tastytrade (el valor por defecto es TPC)


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
    assert reject_reason(snap, 100.0, TODAY, f) is None                           # sin ATR no se puede evaluar: no descarta
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


async def test_portfolio_tickers_get_full_candles_with_open_and_others_do_not():
    repo = BarRepo(Database(":memory:"))
    cfg = TrendSettings()
    series = bars(40)
    ranges = {d: (px + 1, px - 1) for d, px in series}
    opens = {d: px - 0.5 for d, px in series}
    for t in ("AAPL", "KO"):                                              # histórico anterior a la migración v37: con máx./mín., sin apertura
        repo.upsert_ohlc(t, [(d, px, *ranges[d]) for d, px in series])
    candles = FakeCandles(bars={"AAPL": series, "KO": series}, ranges={"AAPL": ranges, "KO": ranges}, opens={"AAPL": opens, "KO": opens})
    await update_history(candles, repo, ["AAPL", "KO"], TODAY, cfg, full_ohlc=["AAPL"])
    assert candles.calls == [["AAPL"]] and candles.days == [cfg.history_days]       # solo el de cartera, y el histórico entero
    d, px = series[-1]
    assert repo.candles("AAPL")[-1] == (d, px - 0.5, px + 1, px - 1, px)             # (día, apertura, máximo, mínimo, cierre)
    assert repo.missing_open(["AAPL", "KO"], 20, 15) == ["KO"]
    await update_history(candles, repo, ["AAPL", "KO"], TODAY, cfg, full_ohlc=["AAPL"])
    assert len(candles.calls) == 1                                                    # y no se vuelve a pedir


def test_a_missing_open_does_not_erase_the_stored_one():
    repo = BarRepo(Database(":memory:"))
    d = TODAY - timedelta(days=1)
    repo.upsert_ohlc("AAPL", [(d, 100.0, 102.0, 98.0, 99.0)])
    repo.upsert_ohlc("AAPL", [(d, 100.5, None, None)])
    assert repo.candles("AAPL") == [(d, 99.0, 102.0, 98.0, 100.5)]


# ---- gestión de puts -----------------------------------------------------------------------------------------
def short_put(ticker="AAPL", strike=90.0, days=30, qty=-2, market_value=-220.0, avg_cost=110.0):
    """2 contratos vendidos a 1,10 $ (avg_cost 110 por contrato); ahora valen 1,10 $ (market_value −220)."""
    o = OptionContract(ticker, TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return Position(ticker, qty, market_value, "Technology", o, avg_cost)


def alerts(status):
    return {a.level: a.text for a in status.alerts}


def test_a_put_without_profile_has_no_management_but_keeps_its_figures():
    p = short_put(market_value=-100.0, days=10)                                    # 55 % ganado y a 10 DTE: con perfil avisaría
    assert alerts(put_status(p, TODAY, 100.0, CFG))
    s = put_status(p, TODAY, 100.0, CFG, managed=False)
    assert s.alerts == () and s.defense is None and s.captured_pct == pytest.approx(54.5, abs=0.1)
    report = manage_puts([p], 100_000.0, TODAY, {"AAPL": 100.0}, CFG, assigned={put_key(p): UNASSIGNED})
    assert report.puts[0].alerts == () and report.puts[0].assigned == UNASSIGNED


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
    itm = put_status(short_put(), TODAY, 89.0, CFG)
    assert "En el dinero" in " ".join(a.text for a in itm.alerts if a.level == "warn")      # con la defensa activada lo dice ella (zona + qué hacer)
    assert "Put testeada" in alerts(put_status(short_put(), TODAY, 91.0, CFG))["info"]       # la zona testeada ya lo dice: sin el aviso genérico de cercanía
    loss = put_status(short_put(market_value=-440.0), TODAY, 100.0, CFG)
    assert loss.captured_pct == pytest.approx(-100.0) and "info" not in alerts(loss)
    assert put_status(short_put(), TODAY, None, CFG).alerts == ()                 # sin precio del subyacente no se avisa


def test_weights_add_the_shares_you_own_and_what_you_would_buy_if_assigned():
    positions = [short_put("AAPL", 90.0, qty=-2), short_put("MSFT", 300.0, qty=-1), Position("AAPL", 100, 9_500.0, "Technology"),
                 Position("KO", 100, 6_000.0, "Staples")]
    weights = {w.ticker: w for w in ticker_weights(positions, 100_000.0)}
    assert set(weights) == {"AAPL", "MSFT"}                                      # solo acciones con puts vendidas
    aapl = weights["AAPL"]
    assert (aapl.stock_value, aapl.put_nominal, aapl.total, aapl.contracts) == (9_500.0, 18_000.0, 27_500.0, 2)
    assert aapl.pct_nlv == pytest.approx(27.5)
    assert weights["MSFT"].pct_nlv == pytest.approx(30.0)
    assert ticker_weights([short_put()], None)[0].pct_nlv is None


def test_the_report_orders_by_expiry_ignores_long_puts_and_counts_important_warnings():
    long_put = Position("AAPL", 1, 50.0, "Technology", OptionContract("AAPL", TODAY + timedelta(days=5), 80.0, OptionRight.PUT), 40.0)
    report = manage_puts([short_put("MSFT", 300.0, days=40), short_put("AAPL", days=10), long_put], 50_000.0, TODAY, {"AAPL": 100.0}, CFG)
    assert [(s.ticker, s.dte) for s in report.puts] == [("AAPL", 10), ("MSFT", 40)]
    assert report.attention >= 1                                                  # AAPL a 10 DTE
    assert manage_puts([], 50_000.0, TODAY, {}, CFG).puts == ()


# ---- perfil de gestión por put: Tastytrade (50 % / 21 DTE) o TPC (mantener hasta el vencimiento) -------------------
def test_tpc_holds_to_expiry_without_take_profit_or_review_alerts():
    half = short_put(market_value=-100.0)                                          # 55 % de la prima ganada
    assert "Recoger beneficios" in alerts(put_status(half, TODAY, 100.0, CFG))["ok"]
    tpc = put_status(half, TODAY, 100.0, CFG, "tpc")
    assert tpc.profile == "tpc" and tpc.alerts == ()                               # ni «recoger» ni aviso: se mantiene
    assert put_status(short_put(days=10), TODAY, 100.0, CFG, "tpc").alerts == ()  # y a 10 DTE sin ganancia tampoco hay «Revisar»
    assert "Revisar" in alerts(put_status(short_put(days=10), TODAY, 100.0, CFG))["warn"]
    near_zero = put_status(short_put(market_value=-10.0), TODAY, 100.0, CFG, "tpc")  # 95 % ganado
    assert "info" not in alerts(near_zero)


def test_tpc_does_not_suggest_rolling_when_in_the_money():
    text = alerts(put_status(short_put(), TODAY, 89.0, CFG, "tpc"))["warn"]
    assert "no se rola" in text and "vender calls" in text
    tasty = " ".join(a.text for a in put_status(short_put(), TODAY, 89.0, CFG).alerts)
    assert "rolar abajo y fuera" in tasty and "no se rola" not in tasty             # Tastytrade sí defiende (la defensa sustituye al «Valora cerrar»)


def test_manage_puts_applies_the_chosen_profile_and_the_configured_default():
    from scanner_opciones.portfolio.management import put_key
    p = short_put(days=10)
    chosen = manage_puts([p], 1_000_000.0, TODAY, {"AAPL": 100.0}, CFG, {put_key(p): "tpc"})
    assert chosen.puts[0].profile == "tpc" and chosen.puts[0].alerts == () and chosen.puts[0].key == put_key(p)
    assert ManagementSettings().default_profile == "tpc"                          # sin elegir, TPC
    default_tpc = ManagementSettings()
    assert manage_puts([p], 1_000_000.0, TODAY, {"AAPL": 100.0}, default_tpc).puts[0].profile == "tpc"
    assert manage_puts([p], 1_000_000.0, TODAY, {"AAPL": 100.0}, default_tpc, {put_key(p): "tastytrade"}).puts[0].profile == "tastytrade"
    assert manage_puts([p], 1_000_000.0, TODAY, {"AAPL": 100.0}, CFG).puts[0].profile == "tastytrade"


# ---- spreads: stop a N × el crédito y riesgo de pin -------------------------------------------------------------
def _spread_position(value: float, days: int = 30) -> Position:
    c = OptionContract("AAPL", TODAY + timedelta(days=days), 90.0, OptionRight.PUT)
    return Position("AAPL", -1, -value, option=c, avg_cost=140.0, hedge_strike=85.0)   # crédito neto 1,40 por acción


def test_spread_stop_fires_when_the_spread_is_worth_twice_the_credit():
    texts = lambda s: [a.text for a in s.alerts]
    assert not any("Stop" in t for t in texts(put_status(_spread_position(270.0), TODAY, 95.0, CFG)))      # vale 2,70 < 2,80
    assert any("Stop" in t for t in texts(put_status(_spread_position(290.0), TODAY, 95.0, CFG)))         # vale 2,90 ≥ 2,80
    plain = Position("AAPL", -1, -290.0, option=_spread_position(1).option, avg_cost=140.0)               # una put sola no tiene este stop
    assert not any("Stop" in t for t in texts(put_status(plain, TODAY, 95.0, CFG)))


def test_spread_pin_risk_only_in_the_last_days_and_near_the_short_strike():
    texts = lambda s: [a.text for a in s.alerts]
    near = put_status(_spread_position(100.0, days=4), TODAY, 91.0, CFG)
    assert any("pin" in t for t in texts(near))                                                              # 4 días y 91 ≤ 90 × 1,02
    assert not any("pin" in t for t in texts(put_status(_spread_position(100.0, days=4), TODAY, 95.0, CFG)))  # lejos del strike
    assert not any("pin" in t for t in texts(put_status(_spread_position(100.0, days=10), TODAY, 91.0, CFG)))  # aún quedan días


def test_spread_panel_shows_one_clear_message_per_situation():
    """Estrategia de salida: tranquilo = «nada que hacer»; stop = cerrar o defender; strike tocado = defender; sin avisos repetidos."""
    live = lambda s, price, delta, **k: put_status(s, TODAY, price, CFG, "tastytrade", live_delta=delta, **k)      # noqa: E731
    calm = live(_spread_position(130.0, days=40), 100.0, 0.20)           # crédito 1,40, vale 1,30: 7 % capturado
    assert [a.level for a in calm.alerts] == ["info"] and calm.alerts[0].short == "Nada que hacer" and "40 días" in calm.alerts[0].text
    assert calm.urgency == 1 and calm.defense.zone == "calm"
    tested = live(_spread_position(200.0, days=40), 91.5, 0.35)          # testeada pero sin tocar el strike (90): no se actúa
    assert [a.short for a in tested.alerts] == ["Nada que hacer"]
    touched = live(_spread_position(200.0, days=40), 89.5, 0.55)         # toca el strike vendido: un único consejo
    assert [a.short for a in touched.alerts] == ["Defender: rolar con crédito"] and touched.urgency == 3
    stopped = live(_spread_position(290.0, days=40), 89.5, 0.60)         # además salta el stop del 200 %: primero el stop
    assert [a.short for a in stopped.alerts] == ["Stop: cerrar o defender", "Defender: rolar con crédito"]
    assert stopped.main_alert.text.startswith("Stop:")
    maxloss = live(_spread_position(480.0, days=40), 84.0, 0.90)         # bajo la put comprada (85)
    assert [a.short for a in maxloss.alerts] == ["Stop: cerrar o defender", "Pérdida máxima"]       # el stop manda; sin «En el dinero» repetido
    time = live(_spread_position(130.0, days=20), 100.0, 0.20)           # 21 DTE sin llegar al objetivo: revisar por tiempo, nada más
    assert [a.short for a in time.alerts] == ["Revisar / rolar"]


def test_total_open_risk_adds_spread_max_loss_and_naked_put_capital():
    spread = _spread_position(100.0)                                        # ancho 5 × 100 − crédito 140 = 360 $
    c = OptionContract("MSFT", TODAY + timedelta(days=30), 50.0, OptionRight.PUT)
    naked = Position("MSFT", -2, -100.0, option=c, avg_cost=60.0)           # 50 × 100 × 2 = 10.000 $
    rep = manage_puts([spread, naked], 20_000.0, TODAY, {"AAPL": 95.0, "MSFT": 60.0}, CFG)
    assert rep.total_risk == pytest.approx(10_360.0) and rep.risk_pct == pytest.approx(51.8) and rep.risk_level == "warn"
    assert manage_puts([spread, naked], 100_000.0, TODAY, {}, CFG).risk_level is None
