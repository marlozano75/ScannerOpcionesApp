"""Filtros de la estrategia de venta de puts: delta, strike fuera del movimiento esperado y cercanía a la media de 50 o a un soporte."""
import math
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import Settings, StrategySettings, TechnicalSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.scanner.technical import TechnicalFilter

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_annual_yield_pct=0)


def snap(strike=90.0, days=36, delta=-0.22, iv=0.30):
    c = OptionContract("AAPL", TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=1.0, ask=1.1, delta=delta, iv=iv, open_interest=500, spread_pct=5.0)


def test_strategy_defaults_are_the_documented_ones_and_validated():
    st = Settings().scanner.strategy
    assert (st.dte_min, st.dte_max, st.min_abs_delta, st.max_abs_delta, st.min_iv_rank) == (30, 45, 0.20, 0.30, 50)
    with pytest.raises(ValueError, match="min_abs_delta"):
        StrategySettings(min_abs_delta=0.4, max_abs_delta=0.3)
    with pytest.raises(ValueError, match="dte_min"):
        StrategySettings(dte_min=50, dte_max=45)


def test_delta_filter_uses_the_absolute_value_and_rejects_unknown_delta():
    c = BASE.with_filters(min_abs_delta=0.20, max_abs_delta=0.30)
    assert reject_reason(snap(delta=-0.22), 100.0, TODAY, c) is None
    assert reject_reason(snap(delta=-0.20), 100.0, TODAY, c) is None                       # los límites entran
    assert "delta" in reject_reason(snap(delta=-0.10), 100.0, TODAY, c)
    assert "delta" in reject_reason(snap(delta=-0.45), 100.0, TODAY, c)
    assert "desconocida" in reject_reason(snap(delta=None), 100.0, TODAY, c)
    assert reject_reason(snap(delta=-0.45), 100.0, TODAY, BASE) is None                    # sin filtro no descarta nada


def test_only_a_minimum_or_only_a_maximum_delta_also_works():
    assert reject_reason(snap(delta=-0.45), 100.0, TODAY, BASE.with_filters(min_abs_delta=0.20)) is None
    assert "delta" in reject_reason(snap(delta=-0.45), 100.0, TODAY, BASE.with_filters(max_abs_delta=0.30))


def test_expected_move_limit_is_price_times_iv_times_sqrt_of_the_days():
    # precio 100, IV 30 %, 36 días: movimiento = 100·0,30·√(36/365) ≈ 9,42 → límite ≈ 90,58
    assert math.isclose(100 * 0.30 * math.sqrt(36 / 365), 9.42, abs_tol=0.01)
    c = BASE.with_filters(expected_move_mult=1.0)
    assert reject_reason(snap(strike=90.0), 100.0, TODAY, c) is None
    assert "movimiento esperado" in reject_reason(snap(strike=91.0), 100.0, TODAY, c)
    assert reject_reason(snap(strike=91.0), 100.0, TODAY, BASE.with_filters(expected_move_mult=0.5)) is None   # medio movimiento
    assert "movimiento esperado" in reject_reason(snap(strike=85.0), 100.0, TODAY, BASE.with_filters(expected_move_mult=2.0))
    assert "sin IV" in reject_reason(snap(iv=None), 100.0, TODAY, c)


def bars(values):
    n = len(values)
    return [(TODAY - timedelta(days=n - i), float(v)) for i, v in enumerate(values)]


def test_near_level_accepts_a_price_close_to_the_50_day_average():
    series = bars([100.0] * 60)                     # MA50 = 100
    cfg = TechnicalSettings()
    c = BASE.with_filters(near_level_pct=3.0)
    assert c.technical_active
    assert TechnicalFilter(c, cfg, {"AAPL": series}, TODAY).reject("AAPL", 98.0, 90.0) is None      # a 2 %
    why = TechnicalFilter(c, cfg, {"AAPL": series}, TODAY).reject("AAPL", 104.0, 90.0)              # a 4 %
    assert why and "media de 50" in why


def test_near_level_without_enough_history_or_support_is_rejected():
    c = BASE.with_filters(near_level_pct=3.0)
    why = TechnicalFilter(c, TechnicalSettings(), {"AAPL": bars([100.0] * 20)}, TODAY).reject("AAPL", 100.0, 90.0)
    assert why and "sin media de 50" in why
