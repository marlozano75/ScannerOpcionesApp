from datetime import date, timedelta

import pytest

from scanner_opciones.metrics import technical as ta

TODAY = date(2026, 10, 6)


def series(values, end=TODAY - timedelta(days=1)):
    """Una barra por día natural acabando en `end`."""
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), float(v)) for i, v in enumerate(values)]


def test_sma_and_ema():
    assert ta.sma([1, 2, 3, 4], 2) == 3.5 and ta.sma([1], 2) is None
    assert ta.ema([1, 2, 3], 3) == 2.0                              # solo la semilla (media simple)
    assert ta.ema([10, 10, 10, 20], 3) == pytest.approx(15.0)      # k = 0,5: 20·0,5 + 10·0,5
    assert ta.ema([1, 2], 3) is None


def test_resample_weekly_and_monthly_keep_the_last_close_of_each_period():
    bars = [(date(2026, 9, 28) + timedelta(days=i), float(i)) for i in range(0, 14)]   # lunes 28-sep ... domingo 11-oct
    weekly = ta.resample(bars, "weekly")
    assert [(d.isoformat(), px) for d, px in weekly] == [("2026-10-04", 6.0), ("2026-10-11", 13.0)]
    monthly = ta.resample(bars, "monthly")
    assert [(d.isoformat(), px) for d, px in monthly] == [("2026-09-30", 2.0), ("2026-10-11", 13.0)]
    assert ta.resample(bars, "daily") == bars
    with pytest.raises(ValueError):
        ta.resample(bars, "yearly")


def test_pivots():
    highs, lows = ta.pivots([1, 2, 5, 2, 1, 2, 0, 2, 3], 2)
    assert highs == [2] and lows == [6]


class TestUnbrokenExtreme:
    def test_uptrend_needs_age_and_progress(self):
        bars = series([50, 40, 42, 45, 47, 50, 55, 60])                  # mínimo 40 hace 7 días, ya +50 %
        assert ta.trend_unbroken_extreme(bars, True, 60.0, TODAY, 7, 5)[0]
        ok, why = ta.trend_unbroken_extreme(bars, True, 60.0, TODAY, 30, 5)
        assert not ok and "solo" in why                                  # el mínimo es demasiado reciente
        ok, why = ta.trend_unbroken_extreme(bars, True, 41.0, TODAY, 7, 5)
        assert not ok and "avance" in why                                # casi sin avance desde el mínimo

    def test_downtrend_is_the_mirror_image(self):
        bars = series([50, 60, 58, 55, 52, 50, 45, 40])
        assert ta.trend_unbroken_extreme(bars, False, 40.0, TODAY, 7, 5)[0]
        assert not ta.trend_unbroken_extreme(bars, True, 40.0, TODAY, 7, 5)[0]

    def test_no_history(self):
        assert ta.trend_unbroken_extreme([], True, 10.0, TODAY, 7, 5) == (False, "sin histórico")


class TestSwings:
    UP = [10, 12, 14, 12, 10, 12, 16, 18, 16, 13, 16, 20, 22, 20, 17, 20, 24, 26, 24, 21, 24, 27]
    DOWN = [-v for v in UP]

    def test_rising_highs_and_lows_is_an_uptrend(self):
        bars = series(self.UP)
        assert ta.trend_swings(bars, True, 27.0, "daily", 2, 60, 3)[0]
        ok, why = ta.trend_swings(bars, False, 27.0, "daily", 2, 60, 3)
        assert not ok and "decrecientes" in why

    def test_breaking_the_last_low_fails_the_check(self):
        assert not ta.trend_swings(series(self.UP), True, 9.0, "daily", 2, 60, 3)[0]

    def test_downtrend_with_falling_swings(self):
        bars = series([100 - v for v in self.UP])
        assert ta.trend_swings(bars, False, 70.0, "daily", 2, 60, 3)[0]

    def test_too_few_pivots(self):
        ok, why = ta.trend_swings(series(self.UP[:8]), True, 18.0, "daily", 2, 60, 3)
        assert not ok and "pocos" in why

    def test_weekly_frame_resamples_before_looking_for_pivots(self):
        # una barra por día: cada valor de UP repetido 7 veces = una barra semanal por valor
        daily = [(TODAY - timedelta(days=7 * (len(self.UP) - i) - j), float(v)) for i, v in enumerate(self.UP) for j in range(7)]
        daily.sort()
        assert ta.trend_swings(daily, True, 27.0, "weekly", 2, 60, 3)[0]


class TestSupport:
    def zones(self, values, **kw):
        params = dict(lookback_days=365, band_pct=1.5, min_touches=3, min_clusters=2, pivot_width=2, cluster_gap_days=5)
        params.update(kw)
        return ta.support_zones(series(values), TODAY, **params)

    # tres rebotes en ~100 (99,7 / 100 / 100,8) con subidas entre medias
    BOUNCES = [110, 108, 104, 100, 104, 108, 112, 108, 104, 99.7, 104, 108, 112, 110, 106, 100.8, 105, 110, 115]

    def test_three_touches_in_separate_clusters_make_a_zone(self):
        zones = self.zones(self.BOUNCES)
        assert len(zones) == 1
        z = zones[0]
        assert (z.low, z.high, z.touches, z.clusters) == (99.7, 100.8, 3, 3)

    def test_best_support_is_the_highest_zone_below_price(self):
        z = self.zones(self.BOUNCES)[0]
        assert ta.best_support([z], 115.0) == z
        assert ta.best_support([z], 100.0) is None                       # el precio ya está dentro/bajo la zona

    def test_needs_enough_touches(self):
        assert self.zones(self.BOUNCES, min_touches=4) == []

    def test_touches_too_close_in_time_are_one_cluster(self):
        assert self.zones(self.BOUNCES, cluster_gap_days=100) == []

    def test_a_close_that_loses_the_level_invalidates_the_zone(self):
        assert len(self.zones(self.BOUNCES + [112, 108, 94, 100, 106, 112])) == 0     # cierra en 94 (-5,7 %) después de los toques
        assert len(self.zones(self.BOUNCES + [112, 108, 99.5, 100, 106, 112])) == 1    # 99,5 sigue dentro de la banda

    def test_only_the_lookback_window_counts(self):
        assert self.zones(self.BOUNCES, lookback_days=5) == []


def test_days_since_touch():
    bars = series([50, 40, 45, 60, 70])
    assert ta.days_since_touch(bars, 45.0, TODAY) == 3                   # el cierre de 45 fue hace 3 días
    assert ta.days_since_touch(bars, 30.0, TODAY) is None                # nunca visitó 30
