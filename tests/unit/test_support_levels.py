from datetime import date, timedelta

from scanner_opciones.config.settings import SupportLevelSettings
from scanner_opciones.metrics.support_levels import nearest_support, support_levels

CFG = SupportLevelSettings()


def candles(path):
    d0 = date(2025, 1, 1)
    return [(d0 + timedelta(days=i), p + 0.5, p + 1.0, p, p + 0.5) for i, p in enumerate(path)]


def vs(bottoms, top=110.0, half=7):
    """Una V por cada mínimo de `bottoms`: baja de `top` al mínimo y vuelve a subir."""
    path = [top]
    for b in bottoms:
        path += [top - (top - b) * k / half for k in range(1, half + 1)] + [b + (top - b) * k / half for k in range(1, half + 1)]
    return path


def test_three_touches_make_a_validated_support():
    lv = support_levels(candles(vs([100, 100.3, 99.8, 100.2])), CFG)
    sup = nearest_support(lv, 109.0, CFG.min_pivots)
    assert sup is not None and sup.pivots == 4 and 99.8 <= sup.low <= sup.high <= 100.3
    assert sup.approaches >= 2 and sup.bounces >= 2


def test_single_touch_is_not_validated_and_lost_level_is_dropped():
    assert nearest_support(support_levels(candles(vs([100])), CFG), 109.0, CFG.min_pivots) is None
    broken = vs([100, 100, 100]) + [95.0] * 8 + [110.0] * 8          # cierra bajo la zona: ya no es soporte
    assert all(lv.high < 100 or lv.low < 100 for lv in support_levels(candles(broken), CFG)) and \
        nearest_support(support_levels(candles(broken), CFG), 110.0, CFG.min_pivots) is None


def test_support_must_be_below_price_and_short_history_is_empty():
    lv = support_levels(candles(vs([100, 100, 100])), CFG)
    assert nearest_support(lv, 99.0, CFG.min_pivots) is None
    assert support_levels(candles([100.0] * 5), CFG) == []
