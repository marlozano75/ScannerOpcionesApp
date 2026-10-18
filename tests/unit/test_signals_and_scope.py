"""Estrategia de venta de puts: señales de entrada «al menos N», delta con solo máximo, distancia en σ, ventana de cotización y explicación del vacío."""
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import Settings, TechnicalSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings, strategy_criteria
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.scanner.impact import active_groups, explain_empty, impact_report
from scanner_opciones.scanner.signals import signals_reject
from scanner_opciones.scanner.technical import TechnicalFilter

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0, min_bid=None)


def snap(strike=90.0, days=36, delta=-0.22, iv=0.30):
    c = OptionContract("AAPL", TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=1.0, ask=1.1, delta=delta, iv=iv, open_interest=500, spread_pct=5.0)


def bars(values):
    n = len(values)
    return [(TODAY - timedelta(days=n - i), float(v)) for i, v in enumerate(values)]


GOOD_IV = TickerInfo("AAPL", underlying_price=100.0, iv30=30.0, hv30=20.0, iv_change_5d=-1.0)      # IV/HV 1,5 y la IV baja
RISING = TickerInfo("AAPL", underlying_price=100.0, iv30=30.0, hv30=20.0, iv_change_5d=2.0)         # IV/HV 1,5 pero la IV sube
NONE_OK = TickerInfo("AAPL", underlying_price=100.0, iv30=21.0, hv30=20.0, iv_change_5d=2.0)


# ---- delta con solo máximo (o solo mínimo) ----------------------------------------------------------------------------
def test_delta_with_only_a_maximum_accepts_every_lower_delta():
    c = BASE.with_filters(max_abs_delta=0.30)
    for d in (-0.02, -0.10, -0.20, -0.30):
        assert reject_reason(snap(delta=d), 100.0, TODAY, c) is None
    assert "delta" in reject_reason(snap(delta=-0.35), 100.0, TODAY, c)
    assert "delta" in reject_reason(snap(delta=-0.05), 100.0, TODAY, BASE.with_filters(min_abs_delta=0.20))


# ---- señales de entrada ------------------------------------------------------------------------------------------------
def test_iv_hv_and_iv_change_are_always_mandatory_even_with_signals():
    for n in (0, 2):
        c = BASE.with_filters(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0, signals_required=n)
        assert reject_reason(snap(), 100.0, TODAY, c, info=GOOD_IV) is None
        assert "ha subido" in reject_reason(snap(), 100.0, TODAY, c, info=RISING)       # no cuentan como señales: obligatorios
        assert signals_reject(c, RISING, None, "AAPL", 100.0) is None                   # y no suman ni restan en las señales


def test_signals_required_is_capped_by_the_number_of_active_signals_and_zero_means_off():
    cfg = TechnicalSettings()
    series = bars([100.0] * 60)                                                          # MA50 = 100: cerca; RSI sin rebote
    c = BASE.with_filters(near_level_pct=3.0, rsi_rebound_max=35.0, signals_required=3)  # 3 exigidas pero solo 2 marcadas: hay que cumplir las 2
    tech = TechnicalFilter(c, cfg, {"AAPL": series}, TODAY)
    assert signals_reject(c, NONE_OK, tech, "AAPL", 100.0) is not None
    assert signals_reject(c.with_filters(signals_required=1), NONE_OK, tech, "AAPL", 100.0) is None   # basta una
    assert signals_reject(BASE.with_filters(signals_required=2), NONE_OK, None, "AAPL", 100.0) is None   # sin señales marcadas no hay nada que exigir


def test_technical_signals_count_together():
    cfg = TechnicalSettings()
    series = bars([100.0] * 60)                                                           # MA50 = 100 y el precio está a su lado; RSI sin rebote
    c = BASE.with_filters(near_level_pct=3.0, rsi_rebound_max=35.0, signals_required=1)
    tech = TechnicalFilter(c, cfg, {"AAPL": series}, TODAY)
    assert dict(tech.signal_results("AAPL", 100.0)) == {"cerca de MA50/soporte": True, "RSI rebota": False}
    assert tech.reject("AAPL", 100.0, 90.0) is None                                       # con signals_required el RSI ya no rechaza por sí solo
    assert signals_reject(c, NONE_OK, tech, "AAPL", 100.0) is None                        # cerca de MA50 = 1 señal
    two = c.with_filters(signals_required=2)
    assert "solo 1 de las 2 señales" in signals_reject(two, NONE_OK, tech, "AAPL", 100.0)
    classic = TechnicalFilter(c.with_filters(signals_required=0), cfg, {"AAPL": series}, TODAY)
    assert "RSI" in classic.reject("AAPL", 100.0, 90.0)                                   # modo clásico: el RSI es obligatorio


def test_the_engine_applies_the_signals_and_the_hard_filters_together():
    infos = {"AAPL": GOOD_IV}
    c = BASE.with_filters(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0, signals_required=2, max_abs_delta=0.30)
    assert len(run_scan([snap()], infos, [], c, TODAY).results) == 1
    assert run_scan([snap()], {"AAPL": RISING}, [], c, TODAY).results == []
    assert run_scan([snap(delta=-0.40)], infos, [], c, TODAY).results == []              # el delta sigue siendo obligatorio




# ---- ventana de la estrategia -------------------------------------------------------------------------------------------
def test_strategy_window_is_quoted_up_to_the_largest_stored_dte_even_with_a_shorter_saved_strategy():
    s = Settings.model_validate({"scanner": {"candidates": {"dte_max": 60}, "strategy": {"dte_min": 30, "dte_max": 45}}})
    c = strategy_criteria(s)
    assert (c.dte_min, c.dte_max) == (30, 60)                       # lo guardado (60) manda sobre la estrategia (45)
    s2 = Settings.model_validate({"scanner": {"candidates": {"dte_max": 35}, "strategy": {"dte_min": 30, "dte_max": 45}}})
    assert strategy_criteria(s2).dte_max == 45                      # la estrategia no se recorta si lo guardado es menor


def test_strategy_criteria_is_the_strategy_window():
    s = Settings()
    c = strategy_criteria(s)
    assert (c.dte_min, c.dte_max, c.strike_below_pct_min) == (30, 45, 5)
    assert c.strike_below_pct_max == s.scanner.candidates.strike_below_pct_max
    assert (c.min_abs_delta, c.max_abs_delta) == (None, 0.30) and c.min_iv_rank == 50 and c.min_yield_pct == 1.0


# ---- explicación del resultado vacío --------------------------------------------------------------------------------------
def test_conflicts_and_the_empty_explanation():
    from dataclasses import replace
    infos = {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}
    # A solo pasa el delta, B solo el OI y C solo el bid size: cada filtro deja un contrato, pero ninguno en común
    a = replace(snap(strike=95.0, delta=-0.25), open_interest=5, bid_size=1)
    b = replace(snap(strike=94.0, delta=-0.05), open_interest=500, bid_size=1)
    c_ = replace(snap(strike=93.0, delta=-0.05), open_interest=5, bid_size=500)
    c = BASE.with_filters(min_abs_delta=0.20, max_abs_delta=0.30, min_oi=100, min_bid_size=100)
    report = impact_report([a, b, c_], infos, c, TODAY)
    pairs = {frozenset(pair) for pair in report.conflicts}
    assert report.final == 0 and frozenset(("Delta del put", "OI mínimo")) in pairs and frozenset(("OI mínimo", "Bid size mínimo")) in pairs
    text = explain_empty(report)
    assert text.startswith("Ningún contrato pasa todos los filtros") and "Se estorban entre sí" in text and "«Delta del put»" in text and "«OI mínimo»" in text
    # con solo dos filtros, quitar uno basta y se dice cuál
    two = impact_report([a, b], infos, BASE.with_filters(max_abs_delta=0.30, min_abs_delta=0.20, min_oi=100), TODAY)
    text2 = explain_empty(two)
    assert "Quitando solo" in text2 and "«Delta del put» recuperarías 1" in text2 and "«OI mínimo» recuperarías 1" in text2
    ok = impact_report([a, b], infos, BASE.with_filters(max_abs_delta=0.30), TODAY)
    assert explain_empty(ok) is None and explain_empty(impact_report([a, b], infos, BASE, TODAY)) is None


def test_with_signals_the_impact_report_has_one_group_for_all_of_them():
    c = BASE.with_filters(min_iv_hv_ratio=1.2, near_level_pct=3.0, rsi_rebound_max=35.0, signals_required=2)
    assert {g[0] for g in active_groups(c)} == {"iv_hv", "signals"}                  # IV/HV es obligatorio: su propio grupo
    assert {g[0] for g in active_groups(c.with_filters(signals_required=0))} == {"iv_hv", "near_level", "rsi"}
