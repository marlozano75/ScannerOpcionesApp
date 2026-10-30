"""Comisiones de IBKR: se restan de la prima en el yield, el descuento (hasta el breakeven), los spreads, la salida de las puts y las ideas."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.community.data import CommunityIdea
from scanner_opciones.community.ideas import evaluate_idea
from scanner_opciones.config.settings import ManagementSettings, Settings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, Position, TickerInfo
from scanner_opciones.metrics.commissions import CommissionModel
from scanner_opciones.portfolio.management import put_status
from scanner_opciones.scanner.criteria import commission_model, criteria_from_settings
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.filters import reject_reason

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
COM = CommissionModel()        # 0,65 $/contrato, mínimo 1 $ por orden, 0,05 $ de tasas
INFO = {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}
BASE = criteria_from_settings(Settings()).with_filters(
    strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0, price_reference=PriceReference.BID, min_bid=None, commission=COM)


def snap(strike, bid, ask, days=30, ticker="AAPL"):
    c = OptionContract(ticker, TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=bid, ask=ask, delta=-0.2, iv=0.3, open_interest=300, spread_pct=5.0)


def test_order_cost_matches_the_real_executions_of_the_account():
    assert COM.order_cost() == pytest.approx(1.05)                                   # 1 contrato suelto: 1,0 + 0,05 (real 1,04–1,07)
    assert COM.order_cost(legs=2) == pytest.approx(1.10)                             # spread en una orden combo (real 0,55 + 0,55)
    assert CommissionModel(contracts=2).order_cost() == pytest.approx(1.40)          # 2 × 0,65 + tasas (real 1,36)
    assert COM.per_share(100) == pytest.approx(0.0105) and COM.per_share(10) == 0.0   # solo opciones de EE. UU. (multiplicador 100)
    assert COM.per_share(100, orders=2) == pytest.approx(0.021)                       # apertura + cierre


def test_default_settings_enable_commissions_and_can_be_disabled():
    assert commission_model(Settings()) == COM
    assert commission_model(Settings.model_validate({"commissions": {"enabled": False}})) is None


def test_yield_and_credit_are_net_of_the_opening_commission():
    out = run_scan([snap(90, 1.00, 1.00)], INFO, [], BASE, TODAY)
    r = out.results[0]
    assert r.reference_price == pytest.approx(1.0) and r.net_price == pytest.approx(1.0 - 0.0105) and r.commission == pytest.approx(0.0105)
    assert r.yield_ref_pct == pytest.approx((1.0 - 0.0105) / 90 * 100)
    gross = run_scan([snap(90, 1.00, 1.00)], INFO, [], BASE.with_filters(commission=None), TODAY).results[0]
    assert gross.yield_ref_pct == pytest.approx(1.0 / 90 * 100) and gross.net_price == pytest.approx(1.0)


def test_exit_commission_is_added_only_when_the_profile_leaves_before_expiry():
    held = run_scan([snap(90, 1.00, 1.00)], INFO, [], BASE, TODAY).results[0]
    early = run_scan([snap(90, 1.00, 1.00)], INFO, [], BASE.with_filters(exit_commission=True), TODAY).results[0]
    assert early.commission == pytest.approx(2 * held.commission) and early.yield_ref_pct < held.yield_ref_pct


def test_discount_is_measured_to_the_breakeven_with_the_net_premium():
    r = run_scan([snap(90, 2.00, 2.00)], INFO, [], BASE, TODAY).results[0]
    assert r.strike_distance_pct == pytest.approx((100 - (90 - (2.0 - 0.0105))) / 100 * 100)     # 11,9895 %, no el 10 % del strike
    # el mínimo se mide con ese descuento: 11 % exige menos strike que antes, 12 % ya no se cumple
    assert reject_reason(snap(90, 2.00, 2.00), 100.0, TODAY, BASE.with_filters(strike_below_pct_min=11)) is None
    assert "fuera de" in reject_reason(snap(90, 2.00, 2.00), 100.0, TODAY, BASE.with_filters(strike_below_pct_min=12))
    # sin comisiones, el descuento vuelve a ser la distancia del strike
    off = BASE.with_filters(commission=None, strike_below_pct_min=11)
    assert "fuera de" in reject_reason(snap(90, 2.00, 2.00), 100.0, TODAY, off)


def test_the_maximum_discount_stays_the_distance_of_the_strike():
    assert "fuera de" in reject_reason(snap(80, 0.5, 0.5), 100.0, TODAY, BASE.with_filters(strike_below_pct_min=0, strike_below_pct_max=15))
    assert reject_reason(snap(86, 0.5, 0.5), 100.0, TODAY, BASE.with_filters(strike_below_pct_min=0, strike_below_pct_max=15)) is None


def test_min_credit_and_min_yield_filters_use_the_net_premium():
    s = snap(90, 0.30, 0.30)          # 30 $ brutos, 28,95 $ netos
    assert reject_reason(s, 100.0, TODAY, BASE.with_filters(min_put_credit=29)) is not None
    assert reject_reason(s, 100.0, TODAY, BASE.with_filters(min_put_credit=28)) is None
    assert reject_reason(s, 100.0, TODAY, BASE.with_filters(min_yield_pct=0.32)) is None          # neto 0,3217 % (bruto 0,3333 %)
    assert "yield" in reject_reason(s, 100.0, TODAY, BASE.with_filters(min_yield_pct=0.33))


def test_spread_credit_pays_one_combo_order_and_the_discount_reaches_its_breakeven():
    spreads = BASE.with_filters(spread_mode=True, spread_widths=(5.0,))
    sp = run_scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)], INFO, [], spreads, TODAY).spreads[0]
    assert sp.credit == pytest.approx(2.0 - 0.6 - 0.011) and sp.commission == pytest.approx(0.011)       # 1,10 $ entre 100 acciones
    assert sp.breakeven == pytest.approx(90 - sp.credit) and sp.discount_pct == pytest.approx((100 - sp.breakeven))
    assert not run_scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)], INFO, [], spreads.with_filters(strike_below_pct_min=12), TODAY).spreads
    assert run_scan([snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)], INFO, [], spreads.with_filters(strike_below_pct_min=11), TODAY).spreads


def short_put(market_value=-100.0, avg_cost=110.0, qty=-2):
    o = OptionContract("AAPL", TODAY + timedelta(days=30), 90.0, OptionRight.PUT)
    return Position("AAPL", qty, market_value, "Technology", o, avg_cost)


def test_captured_premium_discounts_the_closing_commission_only_when_the_strategy_exits_early():
    cfg = ManagementSettings(default_profile="tastytrade")
    plain = put_status(short_put(), TODAY, 100.0, cfg)                       # entrada 1,10, ahora 0,50 → 54,5 %
    net = put_status(short_put(), TODAY, 100.0, cfg, commission=COM)         # recomprar 2 contratos: 1,0 + 0,10 = 1,10 $ → 0,0055 por acción
    assert plain.captured_pct == pytest.approx(54.545, abs=0.01)
    assert net.captured_pct == pytest.approx((1.10 - 0.50 - 1.40 / 200) / 1.10 * 100, abs=0.01)       # 2 contratos: 1,30 + 0,10 = 1,40 $
    assert net.captured_amount == pytest.approx(plain.captured_amount - 1.40)
    tpc = put_status(short_put(), TODAY, 100.0, ManagementSettings(default_profile="tpc"), "tpc", commission=COM)
    assert tpc.captured_pct == pytest.approx(plain.captured_pct)             # TPC mantiene hasta el vencimiento: no hay comisión de salida


def test_take_profit_waits_until_the_net_capture_reaches_the_target():
    cfg = ManagementSettings(default_profile="tastytrade")
    near = short_put(market_value=-110.0)         # ahora 0,55: 50,0 % bruto, justo en el objetivo
    assert any("Recoger" in a.text for a in put_status(near, TODAY, 100.0, cfg).alerts)
    assert not any("Recoger" in a.text for a in put_status(near, TODAY, 100.0, cfg, commission=COM).alerts)    # neto de la comisión de cierre, aún no


def test_idea_premium_yield_and_discount_are_net_but_the_ratio_to_what_the_trader_got_is_gross():
    idea = CommunityIdea(day=date(2026, 10, 1), trader="Ana", ticker="AAPL", strategy="Short Put", expiry=TODAY + timedelta(days=30), short_strike=90.0,
                         long_strike=None, contracts=1, credit=2.0)
    snaps = {("AAPL", idea.expiry, 90.0): snap(90, 2.0, 2.0)}
    view = evaluate_idea(idea, TODAY, snaps, INFO, {}, {"AAPL"}, PriceReference.BID, 25.0, commission=COM)
    assert view.premium_now == pytest.approx(2.0 - 0.0105) and view.status == "valid"       # 100 % de lo que cobró, medido en bruto
    assert view.otm_now_pct == pytest.approx(100 - (90 - view.premium_now))
