from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.broker import ibkr_mapper as m
from scanner_opciones.config.settings import AccountTags, CushionThresholds
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, OptionContract, Position
from scanner_opciones.portfolio.leverage import (
    assignment_exposure, leverage_assignment, long_put_protection, short_put_exposure,
)
from scanner_opciones.portfolio.simulator import SimulatedTrade, simulate

EXP = date(2026, 10, 30)
ACC = AccountSummary("DU1", net_liquidation=100_000, gross_position_value=250_000, cushion_pct=50.0,
                     excess_liquidity=50_000)


def opt(strike, qty, right=OptionRight.PUT, ticker="AAPL"):
    return Position(ticker, qty, 0.0, "Tech", OptionContract(ticker, EXP, strike, right))


def test_short_long_and_nae():
    pos = [
        opt(100, -2),          # short: 100*100*2 = 20_000
        opt(50, -1),           # short: 5_000
        opt(80, +1),           # protección: 8_000
        opt(60, -1, OptionRight.CALL),   # call vendida: no cuenta
        Position("AAPL", 100, 30_000, "Tech"),   # acciones: no cuentan
    ]
    assert short_put_exposure(pos) == 25_000
    assert long_put_protection(pos) == 8_000
    a = assignment_exposure(pos, ACC)
    assert a.nominal_assignment_exposure == 17_000
    assert a.leverage_assignment == pytest.approx(0.17)
    assert a.gross_position_value == 250_000 and a.net_liquidation == 100_000


def test_nae_can_be_negative_when_more_protection():
    a = assignment_exposure([opt(100, +2)], ACC)
    assert a.nominal_assignment_exposure == -20_000 and a.leverage_assignment == pytest.approx(-0.2)


@pytest.mark.parametrize("nlv", [None, 0, -5])
def test_leverage_needs_valid_nlv(nlv):
    assert leverage_assignment(10_000, nlv) is None


def test_no_account_and_no_positions():
    a = assignment_exposure([], None)
    assert a.nominal_assignment_exposure == 0 and a.leverage_assignment is None and a.gross_position_value is None


def test_simulator_reports_leverage_before_after():
    pos = [opt(100, -1)]   # 10_000 -> 0.10x
    c = OptionContract("KO", EXP, 60.0)
    r = simulate(pos, [SimulatedTrade(c, 2, "Staples", 1_000.0)], ACC, CushionThresholds(), date(2026, 9, 29))
    assert r.assignment_before.leverage_assignment == pytest.approx(0.10)
    assert r.assignment_after.nominal_assignment_exposure == 10_000 + 12_000
    assert r.assignment_after.leverage_assignment == pytest.approx(0.22)


def test_mapper_reads_gross_position_value():
    v = [NS(tag="GrossPositionValue", value="33664.0", currency="EUR", account="DU1")]
    s = m.build_account_summary(v, AccountTags(), "DU1", datetime(2026, 9, 29))
    assert s.gross_position_value == 33664.0
