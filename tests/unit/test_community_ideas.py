"""Ideas de la comunidad re-valoradas con los datos de hoy."""
from datetime import date, datetime

import pytest

from scanner_opciones.community.data import CommunityIdea
from scanner_opciones.community.ideas import DROPPED, EXPIRED, ITM, LOWER, NO_DATA, VALID, evaluate_idea, evaluate_ideas, snapshot_key
from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo

TODAY = date(2026, 10, 12)
OPEN = date(2026, 10, 9)
EXPIRY = date(2026, 11, 20)


def idea(ticker="VZ", strike=42.0, long_strike=None, credit=1.0, day=OPEN, expiry=EXPIRY, trader="Juancm"):
    strategy = "Short Put" if long_strike is None else "Bull Put Spread"
    return CommunityIdea(day, trader, ticker, strategy, expiry, strike, long_strike, 1, credit, None)


def snap(ticker, strike, bid, ask, delta=-0.2, expiry=EXPIRY):
    return ContractSnapshot(OptionContract(ticker, expiry, strike), datetime(2026, 10, 12, 10), bid=bid, ask=ask, delta=delta)


def run(i, snaps=(), price=50.0, closes=None, watch=("VZ",), **kw):
    snapshots = {snapshot_key(s.contract.ticker, s.contract.expiry, s.contract.strike): s for s in snaps}
    infos = {i.ticker: TickerInfo(i.ticker, underlying_price=price)} if price is not None else {}
    closes = {i.ticker: closes if closes is not None else [(date(2026, 10, 8), 49.0), (OPEN, 50.0)]}
    return evaluate_idea(i, TODAY, snapshots, infos, closes, set(watch), PriceReference.BID, 25.0, **kw)


def test_a_put_that_still_pays_about_the_same_is_valid():
    v = run(idea(), [snap("VZ", 42.0, 0.95, 1.10)])
    assert v.status == VALID and v.premium_now == pytest.approx(0.95) and v.dte == 39
    assert v.price_open == 50.0 and v.move_pct == pytest.approx(0.0) and v.otm_open_pct == pytest.approx(16.0)
    assert v.yield_open_pct == pytest.approx(1.0 / 42 * 100) and v.yield_now_pct == pytest.approx(0.95 / 42 * 100) and v.delta_now == -0.2


def test_a_lower_premium_is_flagged_and_the_threshold_is_configurable():
    assert run(idea(), [snap("VZ", 42.0, 0.60, 0.70)]).status == LOWER
    assert run(idea(), [snap("VZ", 42.0, 0.60, 0.70)], min_premium_ratio=0.5).status == VALID


def test_a_fall_since_the_opening_or_an_itm_strike_overrides_everything_else():
    fell = run(idea(), [snap("VZ", 42.0, 1.5, 1.7)], price=46.0)                       # −8 % desde el cierre del día de la apertura
    assert fell.status == DROPPED and fell.move_pct == pytest.approx(-8.0)
    itm = run(idea(), [snap("VZ", 42.0, 3.0, 3.2)], price=41.0)
    assert itm.status == ITM and "por debajo del strike" in itm.text


def test_without_a_stored_quote_the_idea_is_not_valued_and_says_why():
    outside = run(idea(), watch=())
    assert outside.status == NO_DATA and "no está en tu watchlist" in outside.text and not outside.in_watchlist
    inside = run(idea(), [])
    assert inside.status == NO_DATA and "sin cotización de esas patas" in inside.text
    assert run(idea(), [snap("VZ", 42.0, 0.0, 0.2)]).status == NO_DATA                  # bid 0: no se puede vender
    assert run(idea(), [snap("VZ", 42.0, 1.0, 1.1)], price=None).status == VALID        # sin precio de hoy no se puede ver la caída


def test_an_expired_idea_is_reported_as_such():
    assert run(idea(expiry=date(2026, 10, 9)), [snap("VZ", 42.0, 1.0, 1.1, expiry=date(2026, 10, 9))]).status == EXPIRED


def test_a_spread_nets_the_ask_of_the_long_leg_and_needs_both_quotes():
    s = idea("PEP", 120.0, 115.0, credit=0.75)
    ok = run(s, [snap("PEP", 120.0, 1.30, 1.40), snap("PEP", 115.0, 0.40, 0.50)], price=130.0, closes=[(OPEN, 130.0)], watch=("PEP",))
    assert ok.status == VALID and ok.premium_now == pytest.approx(1.30 - 0.50)
    assert ok.yield_open_pct == pytest.approx(0.75 / 5 * 100) and ok.yield_now_pct == pytest.approx(0.80 / 5 * 100)
    missing = run(s, [snap("PEP", 120.0, 1.30, 1.40)], price=130.0, closes=[(OPEN, 130.0)], watch=("PEP",))
    assert missing.status == NO_DATA


def test_the_price_at_the_opening_is_the_close_of_that_day_or_the_last_one_before():
    assert run(idea(), [snap("VZ", 42.0, 1.0, 1.1)], closes=[(date(2026, 10, 7), 48.0), (date(2026, 10, 12), 55.0)]).price_open == 48.0
    assert run(idea(), [snap("VZ", 42.0, 1.0, 1.1)], closes=[(date(2026, 10, 12), 55.0)]).price_open is None


def test_ideas_are_sorted_newest_first_and_valid_before_the_rest():
    a, b, c = idea("VZ", day=OPEN), idea("VZ", strike=40.0, day=OPEN), idea("VZ", day=date(2026, 10, 8))
    snaps = {snapshot_key("VZ", EXPIRY, 42.0): snap("VZ", 42.0, 1.0, 1.1)}
    infos = {"VZ": TickerInfo("VZ", underlying_price=50.0)}
    views = evaluate_ideas([c, b, a], TODAY, snaps, infos, {}, {"VZ"}, PriceReference.BID, 25.0)
    assert [(v.idea.day, v.idea.short_strike, v.status) for v in views] == [(OPEN, 42.0, VALID), (OPEN, 40.0, NO_DATA), (date(2026, 10, 8), 42.0, VALID)]


def test_the_idea_is_checked_against_each_profile_with_its_default_values():
    from scanner_opciones.community.fit import strategy_fits
    from scanner_opciones.config.settings import DEFAULT_PRESETS, StrategySettings
    v = run(idea(), [snap("VZ", 42.0, 0.95, 1.10)])          # 39 DTE, strike 16 % por debajo, yield 2,26 %, delta 0,2
    fits = {f.name: f for f in strategy_fits(v, DEFAULT_PRESETS, StrategySettings())}
    assert not fits["Mensual (TPC)"].fits and "descuento" in fits["Mensual (TPC)"].failed[0] and any("DTE 39" in x for x in fits["Mensual (TPC)"].failed)
    assert not fits["Táctico (TPC)"].fits
    assert fits["Tastytrade"].fits and fits["Tastytrade"].unknown == ("IV Rank", "IV Percentile")      # sin ficha de IV no se puede medir
    close = strategy_fits(run(idea(expiry=date(2026, 11, 9)), [snap("VZ", 42.0, 0.95, 1.10, expiry=date(2026, 11, 9))], price=55.0),
                          DEFAULT_PRESETS, StrategySettings())
    assert close[0].fits and close[0].name == "Mensual (TPC)"                                  # 28 DTE, strike 24 % por debajo, yield 2,26 %


def test_a_spread_does_not_use_the_yield_and_an_expired_idea_has_no_fit():
    from scanner_opciones.community.fit import strategy_fits
    from scanner_opciones.config.settings import DEFAULT_PRESETS, StrategySettings
    spread = run(idea(strike=42.0, long_strike=40.0), [snap("VZ", 42.0, 0.95, 1.10), snap("VZ", 40.0, 0.20, 0.30)])
    f = strategy_fits(spread, DEFAULT_PRESETS, StrategySettings())[2]
    assert f.fits and any("spread" in u for u in f.unknown)
    assert strategy_fits(run(idea(expiry=date(2026, 10, 1))), DEFAULT_PRESETS, StrategySettings()) == ()
