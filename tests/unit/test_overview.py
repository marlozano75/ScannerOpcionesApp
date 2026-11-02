"""Estado de la cartera y detalle de «Otros»: acciones, calls y puts compradas sueltas, con su prima capturada."""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import IbkrSettings, ManagementSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, OptionContract, Position
from scanner_opciones.portfolio.management import manage_puts
from scanner_opciones.portfolio.overview import OTHERS, other_positions, portfolio_status

TODAY = date(2026, 10, 29)
EXP = date(2026, 11, 20)


def opt(right, strike, multiplier=100):
    return OptionContract("AAPL", EXP, strike, right, multiplier)


def test_others_exclude_short_puts_and_spreads_but_keep_everything_else():
    positions = [
        Position("AAPL", -1, -200.0, option=opt(OptionRight.PUT, 90.0), avg_cost=300.0),      # put vendida: tabla de gestión
        Position("AAPL", -1, -80.0, option=opt(OptionRight.PUT, 80.0), avg_cost=100.0),
        Position("AAPL", 1, 30.0, option=opt(OptionRight.PUT, 70.0), avg_cost=20.0),          # pata comprada: sin spread con la de 80? sí (70 < 80)
        Position("MSFT", 10, 4000.0, avg_cost=350.0),                                         # acción: 3500 → 4000
        Position("MSFT", -1, -120.0, option=OptionContract("MSFT", EXP, 450.0, OptionRight.CALL), avg_cost=200.0),   # call cubierta
    ]
    rows = other_positions(positions, TODAY, 20_000.0)
    assert {(r.ticker, r.kind) for r in rows} == {("MSFT", "Acción"), ("MSFT", "Call vendida")}
    assert all(r.profile == OTHERS for r in rows)
    stock = next(r for r in rows if r.kind == "Acción")
    assert stock.pnl == pytest.approx(500.0) and stock.captured is None and stock.pct_nlv == pytest.approx(20.0)
    call = next(r for r in rows if r.kind == "Call vendida")
    assert call.premium == pytest.approx(200.0) and call.captured == pytest.approx(80.0) and call.captured_pct == pytest.approx(40.0)
    assert call.dte == 22 and call.entry == pytest.approx(2.0) and call.current == pytest.approx(1.2)


def test_bought_option_has_pnl_but_no_premium_captured():
    p = Position("AAPL", 2, 500.0, option=opt(OptionRight.CALL, 100.0), avg_cost=200.0)
    (row,) = other_positions([p], TODAY, None)
    assert row.kind == "Call comprada" and row.pnl == pytest.approx(100.0) and row.captured is None and row.pct_nlv is None


def test_option_combos_are_detected_and_valued_net():
    c = lambda s, q, mv, avg: Position("AAPL", q, mv, option=opt(OptionRight.CALL, s), avg_cost=avg)
    pt = lambda s, q, mv, avg: Position("AAPL", q, mv, option=opt(OptionRight.PUT, s), avg_cost=avg)
    positions = [c(100.0, 1, 500.0, 400.0), c(110.0, -1, -200.0, 150.0),        # Call Debit Spread: débito 2,50 → 3,00
                 pt(90.0, 2, 160.0, 100.0), pt(80.0, -2, -60.0, 40.0),          # Bear Put Spread x2: débito 0,60 → 0,50
                 c(120.0, -1, -50.0, 80.0), c(125.0, 1, 20.0, 30.0)]            # Call Credit Spread: crédito 0,50 → 0,30
    rows = {r.kind: r for r in other_positions(positions, TODAY, 20_000.0)}
    assert set(rows) == {"Call Debit Spread", "Bear Put Spread", "Call Credit Spread"}
    cds = rows["Call Debit Spread"]
    assert cds.strikes == "100/110" and cds.entry == pytest.approx(2.5) and cds.current == pytest.approx(3.0)
    assert cds.pnl == pytest.approx(50.0) and cds.premium is None
    bps = rows["Bear Put Spread"]
    assert bps.quantity == 2 and bps.pnl == pytest.approx(-20.0) and bps.current == pytest.approx(0.5)
    ccs = rows["Call Credit Spread"]
    assert ccs.premium == pytest.approx(50.0) and ccs.captured == pytest.approx(20.0)


def test_other_positions_carry_numeric_levels_for_the_chart():
    single = other_positions([Position("AAPL", 1, 500.0, option=opt(OptionRight.CALL, 100.0), avg_cost=200.0)], TODAY, None)[0]
    assert single.levels == (100.0,)
    combo = {r.kind: r for r in other_positions(
        [Position("AAPL", 1, 500.0, option=opt(OptionRight.CALL, 100.0), avg_cost=400.0),
         Position("AAPL", -1, -200.0, option=opt(OptionRight.CALL, 110.0), avg_cost=150.0)], TODAY, None)}["Call Debit Spread"]
    assert combo.levels == (100.0, 110.0)
    stock = other_positions([Position("MSFT", 10, 4000.0, avg_cost=350.0)], TODAY, None)[0]
    assert stock.levels == ()


def test_other_chart_and_mini_render_with_closes():
    from scanner_opciones.ui.position_charts import other_chart, other_mini
    op = other_positions([Position("AAPL", 1, 500.0, option=opt(OptionRight.CALL, 100.0), avg_cost=200.0)], TODAY, None)[0]
    closes = [(date(2026, 9, 1), 95.0), (date(2026, 10, 1), 102.0), (TODAY, 104.0)]
    chart, mini = other_chart(closes, op, TODAY), other_mini(closes, op, TODAY)
    assert "<svg" in chart and "strike comprado 100 Call" in chart and "venc." in chart
    assert "<svg" in mini
    spread = {r.kind: r for r in other_positions(
        [Position("AAPL", 1, 500.0, option=opt(OptionRight.CALL, 100.0), avg_cost=400.0),
         Position("AAPL", -1, -200.0, option=opt(OptionRight.CALL, 110.0), avg_cost=150.0)], TODAY, None)}["Call Debit Spread"]
    sc = other_chart(closes, spread, TODAY)
    assert "strike comprado 100 Call" in sc and "strike vendido 110 Call" in sc
    assert 'stroke="var(--red)"' in sc and 'stroke="#2f7de1"' in sc
    assert str(other_mini([], op, TODAY)) == ""          # sin cierres: miniatura vacía


def test_put_status_reports_captured_amount_and_report_totals():
    puts = [Position("AAPL", -2, -300.0, option=opt(OptionRight.PUT, 90.0), avg_cost=300.0)]     # cobró 600, recomprar cuesta 300
    report = manage_puts(puts, 50_000.0, TODAY, {"AAPL": 100.0}, ManagementSettings())
    (s,) = report.puts
    assert s.premium_amount == pytest.approx(600.0) and s.captured_amount == pytest.approx(300.0)
    assert report.premium_total == pytest.approx(600.0) and report.captured_total == pytest.approx(300.0)
    assert report.captured_total_pct == pytest.approx(50.0)


def test_portfolio_status_combines_account_puts_and_others():
    puts = manage_puts([Position("AAPL", -1, -100.0, option=opt(OptionRight.PUT, 90.0), avg_cost=200.0)], 50_000.0, TODAY, {}, ManagementSettings()).puts
    others = other_positions([Position("MSFT", 10, 4000.0, avg_cost=350.0),
                              Position("MSFT", -1, -120.0, option=OptionContract("MSFT", EXP, 450.0, OptionRight.CALL), avg_cost=200.0)], TODAY, 50_000.0)
    acc = AccountSummary("U1", net_liquidation=50_500.0, daily_pnl=500.0, unrealized_pnl=1_000.0, base_currency="EUR")
    st = portfolio_status(acc, others, puts)
    assert st.nlv == 50_500.0 and st.currency == "EUR" and st.daily_pct == pytest.approx(1.0)       # 500 sobre 50.000 de ayer
    assert st.positions == 3 and st.stock_value == pytest.approx(4000.0) and st.stock_pnl == pytest.approx(500.0)
    assert st.premium == pytest.approx(400.0) and st.captured == pytest.approx(180.0)               # put 100 de 200 + call 80 de 200
    empty = portfolio_status(None, [], [])
    assert empty.nlv is None and empty.daily_pct is None and empty.captured_pct is None and empty.currency == "USD"


class FakeIB:
    """El P&L llega tras la suscripción; `pnl()` empieza con NaN y se llena después."""

    def __init__(self, values):
        self.values, self.requests = values, []

    def reqPnL(self, account):
        self.requests.append(account)

    def pnl(self, account):
        return [NS(dailyPnL=self.values[0], unrealizedPnL=self.values[1], realizedPnL=self.values[2])]


async def test_gateway_reads_daily_pnl_once_subscribed_and_tolerates_missing_values():
    gw = IBKRGateway(IbkrSettings(), now=lambda: datetime(2026, 10, 29, 10))
    gw.ib = FakeIB((-125.5, 800.0, 1.7976931348623157e308))       # el float máximo es el «sin valor» de IBKR
    assert await gw._read_pnl("U1") == (-125.5, 800.0, None)
    await gw._read_pnl("U1")
    assert gw.ib.requests == ["U1"]                               # se suscribe una sola vez
    gw2 = IBKRGateway(IbkrSettings(), now=lambda: datetime(2026, 10, 29, 10))
    gw2.ib = object()                                             # sin reqPnL: la cuenta se lee igualmente
    assert await gw2._read_pnl("U1") == (None, None, None)


# ---- gestión de las posiciones que no son puts vendidas (zona por delta, avisos, capturado) -----------------------------------------------------------------
from datetime import timedelta                                       # noqa: E402
from scanner_opciones.portfolio import other_mgmt                    # noqa: E402
from scanner_opciones.portfolio.exits import defaults as exit_defaults  # noqa: E402
from scanner_opciones.portfolio.overview import leg_key              # noqa: E402

CFG = ManagementSettings()


def _assess(positions, nlv=None, price=100.0, rules=None, deltas=None, today=TODAY):
    rows = other_positions(positions, today, nlv, {"AAPL": price, "MSFT": price})
    return [other_mgmt.assess(r, rules, CFG, today, deltas or {}, 0.30) for r in rows]


def _row(rows, kind):
    return next(r for r in rows if r.kind == kind)


def test_short_call_gets_a_zone_from_real_delta_and_the_put_style_alerts():
    c = opt(OptionRight.CALL, 105.0)
    p = Position("AAPL", -1, -50.0, option=c, avg_cost=200.0)            # cobró 2,00 por acción, recomprar cuesta 0,50: 75 % capturado
    row = _row(_assess([p], deltas={leg_key(c): 0.35}), "Call vendida")
    assert row.zone == "tested" and row.delta == 0.35 and row.delta_live and row.take_profit_pct == CFG.take_profit_pct
    assert row.alerts[0].level == "ok" and row.alerts[0].text.startswith("Recoger beneficios") and row.urgency == 2


def test_short_call_in_the_money_warns_and_estimates_delta_without_quotes():
    p = Position("AAPL", -1, -900.0, option=opt(OptionRight.CALL, 95.0), avg_cost=300.0)
    row = _row(_assess([p], price=100.0), "Call vendida")
    assert not row.delta_live and row.delta is not None and row.delta > 0.5          # call 95 con el precio en 100: dentro del dinero
    assert row.zone in ("atm", "deep") and any(a.level == "warn" and a.text.startswith("En el dinero") for a in row.alerts)
    assert not any(a.text.startswith("En pérdida") for a in row.alerts) and row.urgency == 3


def test_long_call_zone_is_inverted_and_applies_target_stop_and_review():
    c = opt(OptionRight.CALL, 100.0)
    winner = Position("AAPL", 1, 500.0, option=c, avg_cost=200.0)          # pagó 2,00, vale 5,00: +150 %
    row = _row(_assess([winner], deltas={leg_key(c): 0.8}), "Call comprada")
    assert row.zone == "ldeep" and row.alerts[0].text.startswith("Recoger beneficios") and row.take_profit_pct == CFG.long_take_profit_pct
    loser = Position("AAPL", 1, 80.0, option=c, avg_cost=200.0)            # vale 0,80: −60 % → stop
    row = _row(_assess([loser], deltas={leg_key(c): 0.2}), "Call comprada")
    assert row.zone == "lfar" and any(a.text.startswith("Stop") for a in row.alerts) and row.urgency == 3
    row = _row(_assess([Position("AAPL", 1, 190.0, option=c, avg_cost=200.0)], today=EXP - timedelta(days=10)), "Call comprada")
    assert any(a.text.startswith("Revisar") for a in row.alerts)            # ≤ 21 DTE sin objetivo


def test_long_call_nearly_worthless_gets_only_an_info_alert():
    c = opt(OptionRight.CALL, 100.0)
    dead = Position("AAPL", 1, 10.0, option=c, avg_cost=200.0)             # pagó 2,00, vale 0,10: −95 %
    row = _row(_assess([dead], today=EXP - timedelta(days=10)), "Call comprada")
    assert [a.level for a in row.alerts] == ["info"] and row.alerts[0].text.startswith("Casi sin valor")


def test_debit_spread_captures_share_of_max_profit_and_has_max_risk():
    long, short = opt(OptionRight.CALL, 100.0), opt(OptionRight.CALL, 110.0)
    positions = [Position("AAPL", 1, 900.0, option=long, avg_cost=500.0), Position("AAPL", -1, -50.0, option=short, avg_cost=100.0)]
    row = _row(_assess(positions, deltas={leg_key(long): 0.9, leg_key(short): 0.2}), "Call Debit Spread")
    assert row.width == 10.0 and row.max_risk == pytest.approx(400.0)        # débito 4,00 × 100
    assert row.captured_pct == pytest.approx((8.5 - 4.0) / (10.0 - 4.0) * 100) and row.take_profit_pct == CFG.debit_take_profit_pct
    assert row.delta == 0.9 and row.zone == "ldeep"                         # zona por la pata comprada
    assert not any(a.level == "ok" for a in row.alerts)                     # 75 % < 90 %


def test_credit_spread_zone_uses_the_short_leg_and_max_risk_is_width_minus_credit():
    short, long = opt(OptionRight.CALL, 100.0), opt(OptionRight.CALL, 110.0)
    positions = [Position("AAPL", -1, -300.0, option=short, avg_cost=500.0), Position("AAPL", 1, 50.0, option=long, avg_cost=100.0)]
    row = _row(_assess(positions, deltas={leg_key(short): 0.55, leg_key(long): 0.2}), "Call Credit Spread")
    assert row.zone == "atm" and row.max_risk == pytest.approx(600.0)        # (10 − 4) × 100
    assert row.premium is not None


def test_tpc_style_keeps_long_positions_without_exit_alerts_and_stocks_have_no_alerts():
    tpc = exit_defaults("tpc", CFG)
    c = opt(OptionRight.CALL, 100.0)
    row = _row(_assess([Position("AAPL", 1, 80.0, option=c, avg_cost=200.0)], rules=tpc), "Call comprada")
    assert not any(a.level == "warn" for a in row.alerts) and row.take_profit_pct is None
    stock = _assess([Position("MSFT", 100, 12_000.0, avg_cost=100.0)], nlv=20_000.0)[0]
    assert stock.zone == "" and stock.alerts == ()
