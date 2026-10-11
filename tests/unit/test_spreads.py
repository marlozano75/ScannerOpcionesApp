from datetime import date

import pytest

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionContract, Position
from scanner_opciones.portfolio.diversification import total_exposure
from scanner_opciones.portfolio.leverage import assignment_exposure
from scanner_opciones.portfolio.spreads import split_spreads, with_spreads

EXP = date(2026, 11, 20)


def put(strike, qty, mv=0.0, cost=None, expiry=EXP, ticker="AAPL"):
    return Position(ticker, qty, mv, "Tech", OptionContract(ticker, expiry, strike, OptionRight.PUT), cost)


def test_pairs_short_with_lower_long_of_the_same_expiry():
    rest, spreads = split_spreads([put(100, -2, -200, 150), put(95, 2, 80, 60)])
    assert rest == [] and len(spreads) == 1
    s = spreads[0]
    assert (s.contracts, s.width) == (2, 5)
    c = s.combined()
    assert (c.market_value, c.avg_cost, c.hedge_strike) == (-120, 90, 95)


def test_exposure_is_the_width_not_the_full_strike():
    pos = with_spreads([put(100, -2), put(95, 2)])
    assert total_exposure(pos) == 5 * 100 * 2
    a = assignment_exposure(pos, None)
    assert a.short_put_exposure == 1_000 and a.long_put_protection == 0 and a.nominal_assignment_exposure == 1_000


def test_partial_match_leaves_the_naked_part_and_ignores_other_expiries_and_higher_strikes():
    rest, spreads = split_spreads([put(100, -3), put(95, 1), put(90, 1, expiry=date(2026, 12, 18)), put(105, 1)])
    assert [s.contracts for s in spreads] == [1]
    assert sorted((p.option.strike, p.quantity) for p in rest) == [(90, 1), (100, -2), (105, 1)]
    assert total_exposure(with_spreads([put(100, -3), put(95, 1)])) == 500 + 2 * 10_000


def test_narrowest_spread_first_and_value_is_prorated():
    _, spreads = split_spreads([put(100, -2, -200), put(95, 1, 50), put(90, 1, 20)])
    assert sorted(s.width for s in spreads) == [5, 10]
    rest, spreads = split_spreads([put(100, -4, -400), put(95, 1, 50)])
    assert rest[0].market_value == pytest.approx(-300) and spreads[0].short.market_value == pytest.approx(-100)
