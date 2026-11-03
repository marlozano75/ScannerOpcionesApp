from datetime import date, timedelta

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.cds import build_call_debit_spreads
from scanner_opciones.scanner.cds_impact import cds_impact_report
from tests.unit.test_cds import TODAY, call, cfg, info


def _many():
    """Varios spreads con características distintas: 120/130 (barato), 130/140, 140/150 (sin OI) y 150/160 (débito alto)."""
    return [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22), call(150, 1.5, 1.6, 0.15, oi=1), call(160, 0.9, 1.0, 0.10)]


def test_final_count_matches_a_real_build_with_all_widths():
    for kw in ({}, {"max_debit_usd": 100}, {"min_long_oi": 50}, {"max_required_move_mult": 0.4}, {"min_max_return_pct": 300}, {"max_leg_spread_pct": 3}):
        cds = cfg(width_options=(10.0, 20.0), **kw)
        real = build_call_debit_spreads(_many(), info(), cds, TODAY)
        report = cds_impact_report(_many(), info(), cds, TODAY)
        assert report.final == len(real), kw


def test_each_filter_reports_what_it_alone_removes():
    cds = cfg(width_options=(10.0, 20.0), min_long_oi=50, max_debit_usd=100)
    report = cds_impact_report(_many(), info(), cds, TODAY)
    by = {f.key: f for f in report.filters}
    assert {"cds_debit", "cds_move", "cds_spread", "cds_oi"} <= set(by)
    assert by["cds_oi"].exclusive >= 1                            # quitar el OI recupera los spreads con una pata sin OI
    assert all(f.alone <= report.window for f in report.filters) and report.final <= report.window
    # un filtro apagado no aparece
    assert "cds_return" not in by and "cds_sma" not in by
    assert "cds_return" in {f.key for f in cds_impact_report(_many(), info(), cfg(min_max_return_pct=50), TODAY).filters}


def test_empty_result_lists_conflicting_filters_and_unknown_ticker_gives_empty_window():
    cds = cfg(width_options=(10.0,), max_debit_usd=100, max_required_move_mult=0.1)
    report = cds_impact_report(_many(), info(), cds, TODAY)
    assert report.final == 0 and report.window >= 1
    assert cds_impact_report(_many(), {}, cds, TODAY).window == 0
