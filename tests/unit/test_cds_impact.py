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
    for kw in ({}, {"max_debit_usd": 100}, {"min_long_oi": 50}, {"max_required_move_mult": 0.4}, {"min_max_return_pct": 300}, {"max_long_spread_pct": 3}):
        cds = cfg(width_options=(10.0, 20.0), **kw)
        real = build_call_debit_spreads(_many(), info(), cds, TODAY)
        report = cds_impact_report(_many(), info(), cds, TODAY)
        assert report.final == len(real), kw


def test_each_filter_reports_what_it_alone_removes():
    cds = cfg(width_options=(10.0, 20.0), min_long_oi=50, min_short_oi=50, max_debit_usd=100)
    report = cds_impact_report(_many(), info(), cds, TODAY)
    by = {f.key: f for f in report.filters}
    assert {"cds_debit", "cds_move", "cds_spread", "cds_oi", "cds_oi_s"} <= set(by)
    assert by["cds_oi_s"].exclusive >= 1                          # quitar el OI de la vendida recupera el spread cuya vendida (150) no tiene OI
    assert all(f.alone <= report.window for f in report.filters) and report.final <= report.window
    # un filtro apagado no aparece
    assert "cds_return" not in by and "cds_sma" not in by
    assert "cds_return" in {f.key for f in cds_impact_report(_many(), info(), cfg(min_max_return_pct=50), TODAY).filters}


def test_empty_result_lists_conflicting_filters_and_unknown_ticker_gives_empty_window():
    cds = cfg(width_options=(10.0,), max_debit_usd=100, max_required_move_mult=0.1)
    report = cds_impact_report(_many(), info(), cds, TODAY)
    assert report.final == 0 and report.window >= 1
    assert cds_impact_report(_many(), {}, cds, TODAY).window == 0


def test_ticker_filters_count_as_one_more_filter():
    from scanner_opciones.scanner.cds_impact import TickerFilter
    cds = cfg(width_options=(10.0,))
    flt = TickerFilter("cds_profit", "Beneficios (12 meses)", "EPS > 0", lambda t: False)
    report = cds_impact_report(_many(), info(), cds, TODAY, ticker_filters=[flt])
    by = {f.key: f for f in report.filters}
    assert report.final == 0 and report.window >= 1
    assert by["cds_profit"].alone == 0 and by["cds_profit"].exclusive >= 1


def test_impact_lists_each_leg_filter_separately():
    from scanner_opciones.scanner.cds_impact import predicates
    keys = [k for k, *_ in predicates(cfg(min_bid_size=5, min_ask_size=5))]
    assert {"cds_oi", "cds_oi_s", "cds_spread", "cds_spread_s", "cds_bsz", "cds_asz"} <= set(keys)
    assert "cds_bsz" not in [k for k, *_ in predicates(cfg())]
