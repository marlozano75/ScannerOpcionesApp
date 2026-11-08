from datetime import date, timedelta

from scanner_opciones.config.settings import DropSettings
from scanner_opciones.metrics.drop import drop_stats

CFG = DropSettings()


def candles(closes, rng=1.0):
    d0 = date(2025, 1, 1)
    return [(d0 + timedelta(days=i), c, c + rng / 2, c - rng / 2, c) for i, c in enumerate(closes)]


def test_flat_ticker_has_no_drop():
    st = drop_stats(candles([100.0] * 300, 0.0), 100.0, 30.0, CFG)
    assert st is not None and st.drop_pct == 0 and st.atr_mult is None


def test_unusual_drop_is_extreme_on_all_three_scales():
    closes = [100 + (i % 3) * 0.5 for i in range(290)] + [100.0] * 10      # ruido pequeño y estable
    st = drop_stats(candles(closes), 90.0, 30.0, CFG)
    assert st.drop_pct > 9 and st.atr_mult > 5 and st.percentile == 100.0 and st.iv_mult > 1.5


def test_usual_drop_has_low_percentile():
    closes = [100 + 5 * ((i // 5) % 2) * (-1) for i in range(300)]          # oscila ±5 %: caer 5 % es lo normal
    st = drop_stats(candles(closes, 2.0), closes[-1], 30.0, CFG)
    assert st.percentile is not None and st.percentile <= 100


def test_insufficient_history_and_missing_iv():
    assert drop_stats(candles([100.0] * 5), 100.0, 30.0, CFG) is None
    st = drop_stats(candles([100.0] * 300), 95.0, None, CFG)
    assert st.iv_mult is None and st.drop_pct > 0
