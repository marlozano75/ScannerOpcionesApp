"""Señales de timing de la estrategia de venta de puts (fase 2): RSI rebotando y bandas de Bollinger, con cierres diarios."""
from datetime import date, timedelta

import pytest

from scanner_opciones.config.settings import Settings, TechnicalSettings
from scanner_opciones.metrics import technical as ta
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.technical import TechnicalFilter

TODAY = date(2026, 10, 6)
BASE = criteria_from_settings(Settings())


def bars(values):
    n = len(values)
    return [(TODAY - timedelta(days=n - i), float(v)) for i, v in enumerate(values)]


def test_rsi_is_100_without_losses_50_without_movement_and_low_after_a_fall():
    assert ta.rsi_series([1, 2, 3, 4], 14) == [None] * 4                                   # sin 14 variaciones
    assert ta.rsi_series(list(range(1, 30)), 14)[-1] == 100.0
    assert ta.rsi_series([10.0] * 30, 14)[-1] == 50.0
    falling = [100 - i for i in range(30)]
    assert ta.rsi_series(falling, 14)[-1] == pytest.approx(0.0)
    mixed = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert ta.rsi_series(mixed, 14)[14] == pytest.approx(70.46, abs=0.1)                  # ejemplo clásico de Wilder


def test_rsi_rebound_needs_a_recent_touch_of_the_level_and_a_rise_today():
    drop_then_up = [100 - 2 * i for i in range(20)] + [62, 63]                            # cae sin parar y rebota hoy
    ok, _ = ta.rsi_rebound(drop_then_up, 35, 10)
    assert ok
    still_falling = [100 - 2 * i for i in range(22)]
    ok, why = ta.rsi_rebound(still_falling, 35, 10)
    assert not ok and "no rebota" in why
    ok, why = ta.rsi_rebound(list(range(1, 40)), 35, 10)                                  # nunca bajó del nivel
    assert not ok and "no ha bajado" in why
    ok, why = ta.rsi_rebound([1.0, 2.0], 35, 10)
    assert not ok and "sin datos" in why


def test_bollinger_bands():
    assert ta.bollinger([1.0] * 5, 20, 2) is None
    low, mid, high = ta.bollinger([10.0] * 20, 20, 2)
    assert (low, mid, high) == (10.0, 10.0, 10.0)
    values = [100.0] * 10 + [102.0] * 10                                                   # media 101, desviación 1
    low, mid, high = ta.bollinger(values, 20, 2)
    assert (low, mid, high) == (pytest.approx(99.0), pytest.approx(101.0), pytest.approx(103.0))


def test_the_technical_filter_applies_rsi_and_bollinger():
    cfg = TechnicalSettings()
    series = bars([100 - 2 * i for i in range(30)] + [42.0, 44.0])                         # caída larga y rebote
    f = lambda **kw: TechnicalFilter(BASE.with_filters(**kw), cfg, {"AAPL": series}, TODAY).reject("AAPL", 44.0, 30.0)   # noqa: E731
    assert f(rsi_rebound_max=35) is None
    assert f(max_bollinger_pctb=0.3) is None                                               # el precio está muy por debajo de la media
    assert "lejos de la banda" in f(max_bollinger_pctb=-0.5)
    assert f(min_bollinger_width_pct=5) is None
    assert "estrechas" in f(min_bollinger_width_pct=90)
    flat = bars([100.0] * 40)
    why = TechnicalFilter(BASE.with_filters(rsi_rebound_max=35), cfg, {"AAPL": flat}, TODAY).reject("AAPL", 100.0, 90.0)
    assert why and "no ha bajado" in why
    short = bars([100.0] * 10)
    why = TechnicalFilter(BASE.with_filters(max_bollinger_pctb=0.3), cfg, {"AAPL": short}, TODAY).reject("AAPL", 100.0, 90.0)
    assert why and "Bollinger" in why


def test_the_criteria_report_the_new_filters_as_technical():
    assert not BASE.technical_active
    for field, value in (("rsi_rebound_max", 35.0), ("max_bollinger_pctb", 0.3), ("min_bollinger_width_pct", 6.0)):
        assert BASE.with_filters(**{field: value}).technical_active
