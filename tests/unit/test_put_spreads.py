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
    strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=50.0, price_reference=PriceReference.BID,
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


def test_best_width_keeps_one_spread_per_short_put():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6), snap(80, 0.1, 0.15)]
    both = scan(legs, spread_widths=(5.0, 10.0))
    best = scan(legs, spread_widths=(5.0, 10.0), best_width=True)
    shorts = [s.short.snapshot.contract.strike for s in best.spreads]
    assert len(shorts) == len(set(shorts)) and len(best.spreads) < len(both.spreads)
    top = {s.short.snapshot.contract.strike: s.return_on_risk_annualized_pct for s in both.spreads}
    assert all(abs(s.return_on_risk_annualized_pct - max(x.return_on_risk_annualized_pct for x in both.spreads
               if x.short.snapshot.contract.strike == s.short.snapshot.contract.strike)) < 1e-9 for s in best.spreads)


def test_min_credit_per_contract_drops_penny_spreads():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]                             # crédito 1,40 → 140 $ por contrato
    assert scan(legs, min_spread_credit=140).spreads and not scan(legs, min_spread_credit=141).spreads


def test_long_leg_needs_open_interest_and_a_two_sided_market():
    ok = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]                               # OI 300, spread ~18 %
    assert scan(ok, min_long_oi=10, max_long_spread_pct=25).spreads
    assert not scan(ok, min_long_oi=500).spreads                                 # OI insuficiente
    assert not scan(ok, max_long_spread_pct=10).spreads                          # spread demasiado abierto
    assert not scan([snap(90, 2.0, 2.2), snap(85, 0.0, 0.3)], max_long_spread_pct=25).spreads   # ask sin bid (caso EOG)
    assert scan([snap(90, 2.0, 2.2), snap(85, 0.0, 0.3)]).spreads                # sin el filtro, pasaba


def test_short_leg_needs_a_real_market_for_its_reference_price():
    wide = [snap(90, 0.05, 2.35), snap(85, 0.5, 0.6)]                           # caso EOG 135/134: la referencia (bid + 25 % del spread) es irreal
    ref = dict(price_reference=PriceReference.BID_PLUS_SPREAD, price_spread_pct=25.0, min_credit_width_pct=None)
    assert scan(wide, **ref).spreads                                              # sin el filtro, salía
    assert not scan(wide, max_short_spread_pct=25, **ref).spreads
    assert scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)], max_short_spread_pct=25, **ref).spreads


def test_combo_spread_is_measured_against_the_credit():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]    # combo 1,40 / 1,70: 0,30 de spread sobre un crédito de 1,40 = 21,4 %
    assert scan(legs, max_combo_spread_pct=25).spreads and not scan(legs, max_combo_spread_pct=20).spreads
    assert scan(legs).spreads                           # sin el filtro, pasaba


# ---- modo «Ambos»: put desnuda si cabe en los límites, si no spread ------------------------------------------------
from types import SimpleNamespace

from scanner_opciones.broker.ibkr_mapper import account_fx
from scanner_opciones.domain.models import Position

MIX = dict(mixed=True, spread_widths=(5.0,), min_yield_pct=0.0)


def mixed(snaps, positions=(), nlv=100_000.0, **over):
    return run_scan(snaps, INFO, list(positions), BASE.with_filters(**{**MIX, **over}), TODAY, nlv_usd=nlv).mixed


def legs(strike=90):
    return [snap(strike, 2.0, 2.2), snap(strike - 5, 0.5, 0.6)]


def test_naked_put_when_the_strike_fits_and_spread_when_it_does_not():
    rows = mixed(legs(), max_naked_strike=100)
    assert [r.kind for r in rows if r.short.snapshot.contract.strike == 90] == ["put"]
    rows = mixed(legs(), max_naked_strike=85)             # 90 > 85: sale el spread
    top = [r for r in rows if r.short.snapshot.contract.strike == 90]
    assert [r.kind for r in top] == ["spread"] and "strike" in top[0].why_spread and round(top[0].weight_pct, 1) == 0.5


def test_weight_is_informative_and_cumulative_with_shares():
    shares = Position("AAPL", 100, 4000.0)                                   # 4 % del NLV ya en acciones
    put = [r for r in mixed(legs(), [shares], max_naked_strike=100) if r.short.snapshot.contract.strike == 90][0]
    assert put.kind == "put" and round(put.weight_pct, 1) == 13.0            # 4 + 9 %; ya no limita, solo se muestra


def test_spread_loss_limit_in_percent_of_nlv():
    rows = mixed(legs(), max_naked_strike=85, max_loss_nlv_pct=0.3)       # pérdida 360 $ = 0,36 % del NLV
    assert not [r for r in rows if r.kind == "spread"]


def test_naked_rows_keep_the_annual_yield_filter_but_spreads_do_not():
    assert not [r for r in mixed(legs(), max_naked_strike=100, min_yield_pct=50.0) if r.kind == "put"]   # rinde ~2 % en el periodo (~27 % anual)
    assert mixed(legs(), max_naked_strike=85, min_yield_pct=50.0)[0].kind == "spread"


def test_rows_ordered_by_annualized_return_on_capital():
    rows = mixed(legs() + [snap(60, 1.0, 1.1), snap(55, 0.2, 0.3)], max_naked_strike=50)
    vals = [r.return_on_capital_annualized_pct for r in rows]
    assert vals == sorted(vals, reverse=True)


def test_account_fx_from_ibkr_values():
    v = lambda tag, value, cur: SimpleNamespace(account="U1", tag=tag, value=value, currency=cur)
    eur = [v("NetLiquidation", "20000", "BASE"), v("NetLiquidation", "20000", "EUR"), v("ExchangeRate", "0.8", "USD")]
    assert account_fx(eur, "U1") == ("EUR", 1.25)
    assert account_fx([v("NetLiquidation", "5", "USD")], "U1") == ("USD", 1.0)
    assert account_fx([v("NetLiquidation", "5", "EUR")], "U1") == ("EUR", None)      # sin tipo de cambio


def test_account_nlv_usd():
    from scanner_opciones.domain.models import AccountSummary
    assert AccountSummary("U1", net_liquidation=20000.0, base_currency="EUR", usd_per_base=1.25).nlv_usd == 25000.0
    assert AccountSummary("U1", net_liquidation=20000.0).nlv_usd is None


# ---- revisión de spreads: por qué cada put vendida sale o no como spread -----------------------------------------
from scanner_opciones.scanner.spread_check import spread_check


def check(snaps, **over):
    c = BASE.with_filters(**{"min_yield_pct": 0.0, **over})
    shorts = run_scan(snaps, INFO, [], c.with_filters(spread_mode=False), TODAY).results
    return spread_check(shorts, snaps, c, 100_000.0)


def test_check_marks_each_condition_and_matches_build_spreads():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]
    row = [r for r in check(legs, min_return_on_risk_pct=10, min_spread_credit=30, min_long_oi=10) if r.strike == 90][0]
    assert row.outcome == "spread" and not row.failed and row.checks["long"].ok and row.checks["ror"].ok is True
    assert [s.short.snapshot.contract.strike for s in scan(legs, min_return_on_risk_pct=10, min_spread_credit=30, min_long_oi=10).spreads] == [90]
    bad = [r for r in check(legs, min_return_on_risk_pct=90) if r.strike == 90][0]
    assert bad.outcome == "no" and bad.checks["ror"].ok is False and "Retorno/riesgo (%)" in bad.failed
    assert not scan(legs, min_return_on_risk_pct=90).spreads


def test_check_reports_missing_long_leg_and_illiquid_long_leg():
    no_leg = [r for r in check([snap(90, 2.0, 2.2), snap(86, 0.5, 0.6)]) if r.strike == 90][0]
    assert no_leg.checks["long"].ok is False and no_leg.outcome == "no"
    eog = [r for r in check([snap(90, 2.0, 2.2), snap(85, 0.0, 0.3)], max_long_spread_pct=25) if r.strike == 90][0]
    assert eog.checks["long_spread"].ok is False and eog.checks["long_spread"].text == "sin bid"


def test_check_in_both_mode_tells_put_from_spread():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]
    assert [r.outcome for r in check(legs, mixed=True, max_naked_strike=100) if r.strike == 90] == ["put"]
    assert [r.outcome for r in check(legs, mixed=True, max_naked_strike=85) if r.strike == 90] == ["spread"]


def test_iv_rank_or_percentile_mode():
    legs = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]
    from dataclasses import replace
    low_rank = [replace(s, iv_rank=25.0, iv_percentile=80.0) for s in legs]
    both = dict(min_iv_rank=30, min_iv_percentile=50)
    assert not scan(low_rank, **both).spreads                       # los dos: el rank 25 < 30 descarta
    assert scan(low_rank, iv_any=True, **both).spreads              # basta uno: el percentile 80 ≥ 50 pasa
    assert not scan([replace(s, iv_rank=25.0, iv_percentile=40.0) for s in legs], iv_any=True, **both).spreads
