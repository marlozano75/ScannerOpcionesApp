from dataclasses import replace
from datetime import date, datetime, timedelta

from scanner_opciones.config.settings import CapitulationSettings
from scanner_opciones.domain.models import VixData
from scanner_opciones.scanner.capitulation import assess, below_keltner

TODAY = date(2026, 11, 6)


def bars(closes, today=TODAY):
    start = today - timedelta(days=len(closes) - 1)
    return [(start + timedelta(days=i), c, c + 1, c - 1, c) for i, c in enumerate(closes)]


def test_below_keltner_detects_a_crash_and_ignores_a_flat_market():
    flat = [100.0] * 60
    crash = flat + [95.0, 88.0]
    assert below_keltner(bars(flat), TODAY) is False
    assert below_keltner(bars(crash), TODAY) is True


def test_below_keltner_needs_enough_recent_complete_bars():
    assert below_keltner(bars([100.0] * 10), TODAY) is None
    assert below_keltner(bars([100.0] * 60, TODAY - timedelta(days=20)), TODAY) is None
    assert below_keltner([(date(2026, 11, 6), 100, None, None, 100)] * 60, TODAY) is None


def vix(spot, front):
    return VixData(spot, [], [(date(2026, 11, 18), front)], datetime(2026, 11, 6))


def test_assess_levels():
    cfg = CapitulationSettings(min_tickers=4)
    wide = [True, True, False, False, False]          # 40 %
    narrow = [True, False, False, False, False]       # 20 % -> justo el umbral
    assert assess(vix(24, 22), wide, cfg).level == "ok"
    assert assess(vix(24, 26), wide, cfg).level == "info"
    assert assess(vix(24, 22), [False] * 5, cfg).level == "info"
    assert assess(vix(24, 26), [False] * 5, cfg).level == "none"
    assert assess(vix(24, 22), narrow, cfg).level == "ok"


def test_vix3m_takes_priority_over_the_front_future():
    cfg = CapitulationSettings(min_tickers=4)
    wide = [True, True, False, False, False]
    v = VixData(24.0, [], [(date(2026, 11, 18), 22.0)], datetime(2026, 11, 6), 25.0)    # VIX bajo el VIX3M aunque sobre el futuro
    out = assess(v, wide, cfg)
    assert out.backwardation is False and "VIX3M" in out.text and out.level == "info"
    assert assess(replace(v, vix3m=23.0), wide, cfg).level == "ok"
    assert "futuro a un mes" in assess(replace(v, vix3m=None), wide, cfg).text


def test_assess_without_data():
    cfg = CapitulationSettings(min_tickers=4)
    assert assess(None, [None, None], cfg).level == "na"
    only_vix = assess(vix(24, 22), [True], cfg)         # cesta insuficiente: solo cuenta el VIX
    assert only_vix.level == "info" and only_vix.breadth_pct is None
