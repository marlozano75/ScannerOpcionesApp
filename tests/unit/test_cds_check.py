"""Consulta de un CDS concreto: parámetros del spread y filtros que no cumple."""
from scanner_opciones.scanner.cds import build_call_debit_spreads
from scanner_opciones.scanner.cds_check import check_cds
from scanner_opciones.scanner.cds_impact import TickerFilter
from tests.unit.test_cds import EXP, TODAY, call, cfg, info

SNAPS = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]


def _check(snaps=SNAPS, c=None, infos=None, expiry=EXP, long_k=130.0, short_k=140.0, **kw):
    c = c or cfg()
    return check_cds(snaps, info() if infos is None else infos, c, TODAY, "ZZZ", expiry, long_k, short_k, **kw)


def test_spread_that_passes_everything_has_no_failed_filter_and_same_numbers_as_the_scanner():
    out = _check(scan_rows=build_call_debit_spreads(SNAPS, info(), cfg(), TODAY))
    [r] = build_call_debit_spreads(SNAPS, info(), cfg(), TODAY)
    assert out.problem is None and out.failed == () and out.in_scan is True
    assert out.result == r


def test_failed_filters_are_listed_but_the_spread_is_still_computed():
    # débito 175 $ con un máximo de 100 $ y una IV baja (el movimiento necesario supera al esperado): el Scanner lo descarta, la consulta lo muestra
    out = _check(c=cfg(max_debit_usd=100), infos=info(iv30=20.0), scan_rows=[])
    assert out.result is not None and out.in_scan is False
    assert {"cds_debit", "cds_move"} <= out.failed_keys
    debit = next(c for c in out.checks if c.key == "cds_debit")
    assert not debit.ok and debit.value == "175 $" and "100" in debit.config


def test_window_filters_dte_and_otm_are_checked():
    out = _check(c=cfg(dte_min=350, dte_max=400, long_otm_pct_min=40))
    assert out.result is not None and {"dte", "otm"} <= out.failed_keys


def test_width_outside_the_chosen_ones_fails_only_without_all_widths():
    snaps = [call(130, 3.9, 4.0, 0.30), call(150, 1.0, 1.2, 0.10)]
    assert "width" in _check(snaps, short_k=150.0).failed_keys                      # all_widths=False y solo 10 $
    assert "width" not in _check(snaps, c=cfg(all_widths=True), short_k=150.0).failed_keys


def test_ticker_filters_count_as_one_more_check():
    out = _check(ticker_filters=[TickerFilter("cds_liq", "Liquidez de las opciones", "≥ 4", lambda t: False)])
    assert out.failed_keys == {"cds_liq"}


def test_missing_leg_gives_a_reason_and_hints_with_the_stored_strikes():
    out = _check(short_k=145.0)
    assert out.result is None and "vendida" in out.problem
    assert any("130" in h and "140" in h for h in out.hints) and any("Vencimientos" in h for h in out.hints)
    assert "comprada" in _check(long_k=120.0).problem


def test_reasons_when_the_spread_cannot_be_built():
    assert "mercado válido" in _check([call(130, 0, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]).problem
    assert "delta" in _check([call(130, 3.9, 4.0, None), call(140, 2.2, 2.4, 0.22)]).problem
    assert "delta neta" in _check([call(130, 3.9, 4.0, 0.25), call(140, 2.2, 2.4, 0.25)]).problem
    assert "precio" in _check(infos={}).problem
    assert "mayor" in _check(long_k=140.0, short_k=130.0).problem


def test_all_ok_but_not_in_the_scan_rows_is_reported_through_in_scan():
    # con `best_width` / `top_per_ticker` el escaneo real puede dejar fuera un spread que cumple todos los filtros
    out = _check(scan_rows=[])
    assert out.failed == () and out.in_scan is False
