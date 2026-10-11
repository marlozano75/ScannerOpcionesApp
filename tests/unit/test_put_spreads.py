"""Modo spread del scanner: emparejado de la pata comprada, crédito conservador y filtros de riesgo."""
from datetime import date, datetime, timedelta

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.engine import run_scan

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(
    strike_below_pct_min=0, dte_min=1, dte_max=60, min_annual_yield_pct=50.0, price_reference=PriceReference.BID,
    min_bid=None, spread_mode=True, spread_widths=(5.0,))
INFO = {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}


def snap(strike, bid, ask, days=30, ticker="AAPL"):
    c = OptionContract(ticker, TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=bid, ask=ask, delta=-0.2, iv=0.3, open_interest=300, spread_pct=5.0)


def scan(snaps, **over):
    return run_scan(snaps, INFO, [], BASE.with_filters(**over), TODAY)


def test_spread_credit_uses_bid_of_the_short_and_ask_of_the_long():
    out = scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)])
    sp = out.spreads[0]
    assert (sp.width, round(sp.credit, 2), round(sp.max_loss, 2)) == (5.0, 1.4, 360.0)
    assert round(sp.credit_width_pct, 1) == 28.0 and round(sp.return_on_risk_pct, 1) == 38.9
    assert round(sp.breakeven, 2) == 88.6 and round(sp.breakeven_distance_pct, 1) == 11.4


def test_yield_over_strike_does_not_apply_in_spread_mode():
    # el put de 90 con bid 2,0 rinde ~27 % anual < 50 %: en modo put se descartaría, en modo spread no
    assert scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]).spreads
    assert not run_scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)], INFO, [], BASE.with_filters(spread_mode=False), TODAY).results


def test_no_long_leg_at_that_width_gives_no_spread():
    assert scan([snap(90, 2.0, 2.2), snap(86, 0.5, 0.6)]).spreads == []
    assert scan([snap(90, 2.0, 2.2), snap(85, 0.0, 0.0)]).spreads == []        # sin ask


def test_long_leg_must_share_ticker_and_expiry():
    assert scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6, days=37)]).spreads == []


def test_risk_filters():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]                             # crédito/ancho 28 %, pérdida 360 $, ror 38,9 %
    assert scan(legs, min_credit_width_pct=25).spreads and not scan(legs, min_credit_width_pct=30).spreads
    assert scan(legs, max_spread_loss=400).spreads and not scan(legs, max_spread_loss=300).spreads
    assert scan(legs, min_return_on_risk_pct=35).spreads and not scan(legs, min_return_on_risk_pct=40).spreads


def test_several_widths_and_ranking_by_annualized_return_on_risk():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6), snap(80, 0.1, 0.15)]
    out = scan(legs, spread_widths=(5.0, 10.0))
    assert sorted((s.short.snapshot.contract.strike, s.width) for s in out.spreads) == [(85, 5.0), (90, 5.0), (90, 10.0)]
    ann = [s.return_on_risk_annualized_pct for s in out.spreads]
    assert ann == sorted(ann, reverse=True)
