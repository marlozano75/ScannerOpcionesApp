import math

import pytest

from scanner_opciones.metrics.spread import mid_price, spread_pct
from scanner_opciones.metrics.yields import (
    annualized_yield_pct,
    gross_yield_pct,
    strike_distance_pct,
)


class TestMidAndSpread:
    def test_mid(self):
        assert mid_price(1.0, 1.2) == pytest.approx(1.1)

    @pytest.mark.parametrize("bid,ask", [(None, 1), (1, None), (-1, 1), (1, -1), (2, 1), (1, 0), (math.nan, 1)])
    def test_mid_invalid(self, bid, ask):
        assert mid_price(bid, ask) is None

    def test_bid_zero_is_valid(self):
        assert mid_price(0, 0.10) == pytest.approx(0.05)

    def test_spread_pct(self):
        # (1.2-1.0)/1.1*100
        assert spread_pct(1.0, 1.2) == pytest.approx(18.1818, rel=1e-4)

    def test_spread_zero(self):
        assert spread_pct(1.0, 1.0) == 0

    def test_spread_invalid(self):
        assert spread_pct(-1, 1) is None


class TestYields:
    def test_gross_yield(self):
        # mid 1.10 sobre strike 100 -> 1.1%
        assert gross_yield_pct(1.0, 1.2, 100) == pytest.approx(1.1)

    def test_gross_yield_exactly_one_percent(self):
        assert gross_yield_pct(0.9, 1.1, 100) == pytest.approx(1.0)

    @pytest.mark.parametrize("strike", [0, -5, None])
    def test_gross_yield_bad_strike(self, strike):
        assert gross_yield_pct(1, 1.2, strike) is None

    def test_gross_yield_bad_quote(self):
        assert gross_yield_pct(-1, -1, 100) is None

    def test_annualized(self):
        assert annualized_yield_pct(1.0, 30) == pytest.approx(365 / 30)

    @pytest.mark.parametrize("y,dte", [(None, 30), (1.0, 0), (1.0, -3), (1.0, None)])
    def test_annualized_invalid(self, y, dte):
        assert annualized_yield_pct(y, dte) is None

    def test_strike_distance(self):
        assert strike_distance_pct(100, 80) == pytest.approx(20)

    def test_strike_above_price_is_negative(self):
        assert strike_distance_pct(100, 110) == pytest.approx(-10)

    @pytest.mark.parametrize("p,s", [(0, 5), (None, 5), (5, None)])
    def test_strike_distance_invalid(self, p, s):
        assert strike_distance_pct(p, s) is None


class TestReferencePrice:
    def test_modes(self):
        from scanner_opciones.domain.enums import PriceReference as R
        from scanner_opciones.metrics.yields import gross_yield_ref_pct, reference_price
        assert reference_price(0.50, 1.10, R.BID) == 0.50
        assert reference_price(0.50, 1.10, R.MID) == pytest.approx(0.80)
        assert reference_price(0.50, 1.10, R.BID_PLUS_SPREAD, 25) == pytest.approx(0.65)
        assert reference_price(0.50, 1.10, R.BID_PLUS_SPREAD, 0) == pytest.approx(0.50)     # X=0 -> bid
        assert reference_price(0.50, 1.10, R.BID_PLUS_SPREAD, 50) == pytest.approx(0.80)    # X=50 -> mid
        assert gross_yield_ref_pct(0.50, 1.10, 20, R.BID_PLUS_SPREAD, 25) == pytest.approx(3.25)
        assert gross_yield_ref_pct(0.50, 1.10, 20, R.BID) == pytest.approx(2.5)
        assert gross_yield_ref_pct(0.50, 1.10, 20, R.MID) == pytest.approx(4.0)

    def test_x_is_clamped_and_bad_quotes_rejected(self):
        from scanner_opciones.domain.enums import PriceReference as R
        from scanner_opciones.metrics.yields import gross_yield_ref_pct, reference_price
        assert reference_price(0.50, 1.10, R.BID_PLUS_SPREAD, 250) == pytest.approx(1.10)   # máx. = ask
        assert reference_price(0.50, 1.10, R.BID_PLUS_SPREAD, -10) == pytest.approx(0.50)   # mín. = bid
        for mode in R:
            assert reference_price(-1, 1.1, mode) is None
            assert reference_price(None, 1.1, mode) is None
            assert reference_price(1.2, 1.1, mode) is None                                   # bid > ask
            assert gross_yield_ref_pct(0.5, 1.1, 0, mode) is None
