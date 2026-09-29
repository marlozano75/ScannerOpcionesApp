import math

import pytest

from scanner_opciones.metrics.iv_stats import iv_percentile, iv_rank
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


class TestIvStats:
    HIST = [0.20, 0.30, 0.40, 0.50]

    def test_rank_middle(self):
        assert iv_rank(0.35, self.HIST) == pytest.approx(50)

    def test_rank_bounds_clamped(self):
        assert iv_rank(0.10, self.HIST) == 0
        assert iv_rank(0.90, self.HIST) == 100

    def test_rank_flat_history(self):
        assert iv_rank(0.3, [0.3, 0.3]) is None

    def test_rank_empty_or_none(self):
        assert iv_rank(0.3, []) is None
        assert iv_rank(None, self.HIST) is None
        assert iv_rank(math.nan, self.HIST) is None

    def test_rank_ignores_nan_in_history(self):
        assert iv_rank(0.35, [0.2, math.nan, None, 0.5]) == pytest.approx(50)

    def test_percentile(self):
        # 0.20 y 0.30 son < 0.35 -> 2/4
        assert iv_percentile(0.35, self.HIST) == pytest.approx(50)

    def test_percentile_strictly_less(self):
        assert iv_percentile(0.30, self.HIST) == pytest.approx(25)

    def test_percentile_extremes(self):
        assert iv_percentile(0.01, self.HIST) == 0
        assert iv_percentile(0.99, self.HIST) == 100

    def test_percentile_empty(self):
        assert iv_percentile(0.3, []) is None
        assert iv_percentile(None, self.HIST) is None


class TestIvRankHighLow:
    CLOSES = [0.30, 0.40, 0.35]
    HIGHS = [0.32, 0.50, 0.36]
    LOWS = [0.25, 0.38, 0.33]

    def test_range_uses_daily_high_and_low(self):
        # rango 0.25 .. 0.50 (no 0.30 .. 0.40 de los cierres)
        assert iv_rank(0.375, self.CLOSES, self.HIGHS, self.LOWS) == pytest.approx(50)
        assert iv_rank(0.375, self.CLOSES) == pytest.approx(75)     # sin máx/mín: solo cierres

    def test_missing_high_low_falls_back_to_close(self):
        # la barra 1 no tiene máx/mín: cuenta su cierre; el resto amplía el rango
        highs = [None, 0.50, None]
        lows = [None, None, 0.20]
        assert iv_rank(0.35, self.CLOSES, highs, lows) == pytest.approx((0.35 - 0.20) / (0.50 - 0.20) * 100)

    def test_nan_high_low_ignored_and_clamped(self):
        assert iv_rank(0.35, self.CLOSES, [math.nan, math.nan, math.nan], [None, None, None]) == pytest.approx(50)
        assert iv_rank(0.90, self.CLOSES, self.HIGHS, self.LOWS) == 100      # acotado
        assert iv_rank(0.10, self.CLOSES, self.HIGHS, self.LOWS) == 0

    def test_percentile_still_uses_closes(self):
        assert iv_percentile(0.375, self.CLOSES) == pytest.approx(2 / 3 * 100)
