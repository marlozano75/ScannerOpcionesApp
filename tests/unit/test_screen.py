import pytest
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.screen import DISCARD, DOUBTFUL, NO_DATA, OK, best_put_yield_pct, iv_used_pct, screen_ticker


def crit(min_yield=1.0, below_min=5.0, dte_max=45):
    return ScanCriteria(strike_below_pct_min=below_min, strike_below_pct_max=30, min_yield_pct=min_yield, dte_min=30, dte_max=dte_max)


def info(price=100.0, iv30=40.0, expiry_ivs=None):
    return TickerInfo("ZZZ", underlying_price=price, iv30=iv30, expiry_ivs=expiry_ivs)


def test_best_yield_grows_with_iv_and_dte_and_falls_with_the_discount():
    base = best_put_yield_pct(100.0, 40.0, crit())
    assert best_put_yield_pct(100.0, 60.0, crit()) > base
    assert best_put_yield_pct(100.0, 40.0, crit(dte_max=90)) > base
    assert best_put_yield_pct(100.0, 40.0, crit(below_min=15.0)) < base


def test_iv_used_is_the_highest_known_one():
    assert iv_used_pct(info(iv30=30.0)) == 30.0
    assert iv_used_pct(info(iv30=30.0, expiry_ivs={"2026-12-18": 0.55, "2027-01-15": 0.35})) == pytest.approx(55.0)
    assert iv_used_pct(TickerInfo("ZZZ", underlying_price=10.0)) is None


def test_ok_doubtful_and_discard_depend_on_the_tolerance():
    best = best_put_yield_pct(100.0, 40.0, crit())
    assert screen_ticker("ZZZ", info(), [("A", crit(min_yield=best * 0.9))]).status == OK
    assert screen_ticker("ZZZ", info(), [("A", crit(min_yield=best * 2))]).status == DOUBTFUL          # con 3× sí podría llegar
    assert screen_ticker("ZZZ", info(), [("A", crit(min_yield=best * 4))]).status == DISCARD
    assert screen_ticker("ZZZ", info(), [("A", crit(min_yield=best * 2))], tolerance=1.5).status == DISCARD


def test_a_ticker_is_discarded_only_if_every_put_profile_discards_it():
    best = best_put_yield_pct(100.0, 40.0, crit())
    easy, hard = ("Fácil", crit(min_yield=best * 0.5)), ("Difícil", crit(min_yield=best * 10))
    assert screen_ticker("ZZZ", info(), [hard, easy]).status == OK
    only_hard = screen_ticker("ZZZ", info(), [hard, ("Otro", crit(min_yield=best * 5))])
    assert only_hard.status == DISCARD and only_hard.best.profile == "Otro"          # el perfil más cercano


def test_without_price_iv_or_profiles_there_is_no_verdict():
    assert screen_ticker("ZZZ", info(price=None), [("A", crit())]).status == NO_DATA
    assert screen_ticker("ZZZ", TickerInfo("ZZZ", underlying_price=50.0), [("A", crit())]).status == NO_DATA
    assert screen_ticker("ZZZ", info(), []).status == NO_DATA
    assert screen_ticker("ZZZ", None, [("A", crit())]).status == NO_DATA
