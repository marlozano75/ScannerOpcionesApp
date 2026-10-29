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
