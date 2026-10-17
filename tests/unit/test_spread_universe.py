"""Fuentes aptas para spreads: ETF exentos de calidad y tickers «solo spread» (nunca put desnuda)."""
import json
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import Settings, SpreadUniverseSettings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.quality import is_exempt, spread_universe_reject, ticker_quality_reject

CFG = SpreadUniverseSettings()
QCFG = Settings().scanner.quality
BASE = criteria_from_settings(Settings())


# ---- ETF exentos de la calidad de la empresa --------------------------------------------------
def test_etf_without_eps_passes_company_filters_but_a_stock_does_not():
    c = BASE.with_filters(require_profitable=True, min_positive_quarters=3, max_liabilities_to_equity=1.0, require_positive_fcf=True)
    assert ticker_quality_reject(TickerInfo("SPY"), c, QCFG.exempt_entries) is None
    assert ticker_quality_reject(TickerInfo("ACME"), c, QCFG.exempt_entries) is not None
    assert is_exempt(TickerInfo("QQQ"), QCFG.exempt_entries) and not is_exempt(TickerInfo("QQQQ"), QCFG.exempt_entries)


def test_etf_still_needs_option_liquidity():
    c = BASE.with_filters(min_option_liquidity=3)
    assert ticker_quality_reject(TickerInfo("SPY", option_liquidity=2), c, QCFG.exempt_entries) is not None
    assert ticker_quality_reject(TickerInfo("SPY", option_liquidity=4), c, QCFG.exempt_entries) is None


def test_sector_names_never_match_the_exact_ticker_entries():
    assert not is_exempt(TickerInfo("ACME", sector="=ACME"), ("=SPY",))


# ---- fuentes aptas para spreads: liquidez y precio -----------------------------------------------
def test_spread_universe_filter_is_liquidity_and_price_only():
    assert spread_universe_reject(TickerInfo("X", option_liquidity=3, underlying_price=50), 3, 30) is None
    assert spread_universe_reject(TickerInfo("X", option_liquidity=2, underlying_price=50), 3, 30) is not None
    assert spread_universe_reject(TickerInfo("X", underlying_price=50), 3, 30) is not None       # sin liquidez no cumple
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4, underlying_price=20), 3, 30) is not None
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4), 3, 30) is None            # sin precio no se descarta
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4, underlying_price=600), 3, 30, 500) is not None


# ---- solo spread, nunca put desnuda ---------------------------------------------------------------
TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
SBASE = BASE.with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0.0, price_reference=PriceReference.BID,
                          min_bid=None, spread_mode=True, spread_widths=(5.0,), best_width=False)
INFO = {"ZZZ": TickerInfo("ZZZ", underlying_price=100.0)}


def snap(strike, bid, ask, ticker="ZZZ"):
    c = OptionContract(ticker, TODAY + timedelta(days=30), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=bid, ask=ask, delta=-0.2, iv=0.3, open_interest=300, spread_pct=5.0)


LEGS = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]


def test_spread_only_ticker_is_excluded_from_put_mode_but_not_spread_mode():
    only = frozenset({"ZZZ"})
    put = run_scan(LEGS, INFO, [], SBASE.with_filters(spread_mode=False), TODAY, spread_only=only, include_rejections=True)
    assert put.results == [] and "solo spreads" in next(iter(put.rejections.values()))
    assert run_scan(LEGS, INFO, [], SBASE.with_filters(spread_mode=False), TODAY).results       # sin la marca sí sale
    assert run_scan(LEGS, INFO, [], SBASE, TODAY, spread_only=only).spreads


def test_both_mode_shows_only_the_spread_for_a_spread_only_ticker_even_under_the_naked_limit():
    c = SBASE.with_filters(mixed=True, max_naked_strike=100.0)       # 90 ≤ 100: normalmente sería put desnuda
    normal = run_scan(LEGS, INFO, [], c, TODAY, nlv_usd=100_000.0).mixed
    assert {r.kind for r in normal} == {"put"}
    only = run_scan(LEGS, INFO, [], c, TODAY, nlv_usd=100_000.0, spread_only=frozenset({"ZZZ"})).mixed
    assert only and {r.kind for r in only} == {"spread"} and "solo spread" in only[0].why_spread
