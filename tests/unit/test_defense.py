"""Defensa de las puts vendidas (portfolio/defense.py, metrics/greeks.py): zonas por delta, avisos y búsqueda del mejor roll con crédito neto."""
from dataclasses import replace
from datetime import timedelta

import pytest

from scanner_opciones.config.settings import DefenseSettings, ManagementSettings
from scanner_opciones.metrics import greeks
from scanner_opciones.portfolio import defense as dfn
from scanner_opciones.portfolio import exits
from scanner_opciones.portfolio.defense import ATM, CALM, DEEP, TESTED, DefenseInput, RollQuote
from scanner_opciones.portfolio.management import put_status
from tests.unit.test_atr_and_management import TODAY, short_put

CFG = DefenseSettings()


# ---- Black-Scholes -------------------------------------------------------------------------------------------
def test_implied_vol_round_trips_and_delta_is_sane():
    price = greeks.put_price(100.0, 95.0, 30 / 365, 0.35, 0.04)
    assert greeks.implied_vol(price, 100.0, 95.0, 30 / 365, 0.04) == pytest.approx(0.35, abs=1e-4)
    assert greeks.implied_vol(0.0, 100.0, 95.0, 30 / 365) is None                     # sin precio no hay IV
    assert greeks.implied_vol(6.0, 90.0, 100.0, 30 / 365) is None                      # por debajo del intrínseco (10)
    atm = greeks.estimate_abs_delta(100.0, 100.0, 30, greeks.put_price(100.0, 100.0, 30 / 365, 0.3))
    assert 0.45 < atm < 0.55
    assert greeks.estimate_abs_delta(100.0, 70.0, 30, 0.05, fallback_iv=0.3) < 0.05
    assert greeks.estimate_abs_delta(100.0, 120.0, 30, 20.0, fallback_iv=0.3) > 0.9   # muy dentro del dinero
    assert greeks.estimate_abs_delta(None, 100.0, 30, 1.0) is None and greeks.estimate_abs_delta(100.0, 100.0, 30, None, None) is None


def test_the_delta_falls_back_to_the_iv30_when_the_price_has_no_extrinsic():
    d = greeks.estimate_abs_delta(80.0, 100.0, 5, 20.0, fallback_iv=0.40)      # precio = intrínseco: la IV no se puede despejar
    assert d is not None and d > 0.9


# ---- zonas y avisos ------------------------------------------------------------------------------------------
def inp(**kw):
    base = dict(expiry=TODAY + timedelta(days=30), strike=100.0, dte=30, underlying=110.0, entry=2.0, current=0.6, iv_rank=25.0, iv30=0.30, today=TODAY)
    base.update(kw)
    return DefenseInput(**base)


def priced(spot, strike=100.0, dte=30, iv=0.30):
    return greeks.put_price(spot, strike, dte / 365, iv, CFG.risk_free_rate)


def texts(d):
    return " ".join(t for _, t in d.steps)


def test_zones_follow_the_delta():
    assert dfn.zone_of(0.12, 110.0, 100.0, CFG) == CALM and dfn.zone_of(0.35, 103.0, 100.0, CFG) == TESTED
    assert dfn.zone_of(0.55, 99.0, 100.0, CFG) == ATM and dfn.zone_of(0.80, 90.0, 100.0, CFG) == DEEP
    assert dfn.zone_of(None, 95.0, 100.0, CFG) == ATM and dfn.zone_of(None, 105.0, 100.0, CFG) == CALM      # sin delta, por el precio


def test_a_calm_put_gets_no_advice_and_no_price_means_no_assessment():
    d = dfn.assess(inp(underlying=115.0, current=0.2), CFG)
    assert d.zone == CALM and d.steps == ()
    assert dfn.assess(inp(underlying=None), CFG) is None


def test_tested_put_waits_or_sells_the_call_and_does_not_roll_yet():
    d = dfn.assess(inp(underlying=103.5, current=priced(103.5)), CFG)
    assert d.zone == TESTED and 0.30 <= d.delta < 0.50
    assert "vende la call" in texts(d) and "Rolar ahora suele rendir poco" in texts(d) and "rolar abajo y fuera" not in texts(d)


def test_atm_put_is_the_moment_to_roll_down_and_out():
    d = dfn.assess(inp(underlying=98.0, current=priced(98.0)), CFG)
    assert d.zone == ATM and d.extrinsic > 1.0
    assert any(level == "warn" and "rolar abajo y fuera" in text and "30–60 DTE" in text for level, text in d.steps)


def test_deep_itm_put_rolls_out_only_or_takes_the_assignment():
    d = dfn.assess(inp(underlying=90.0, current=10.4, dte=10, expiry=TODAY + timedelta(days=10)), CFG)
    assert d.zone == DEEP and "solo hacia fuera" in texts(d) and "asignación" in texts(d)


def test_21_dte_and_almost_no_extrinsic_and_the_dividend_are_flagged():
    soon = dfn.assess(inp(underlying=99.0, dte=15, expiry=TODAY + timedelta(days=15), current=priced(99.0, dte=15)), CFG)
    assert any("Quedan 15 días" in t and "gamma" in t for _, t in soon.steps)
    assert "Quedan" not in texts(dfn.assess(inp(underlying=99.0, current=priced(99.0)), CFG))                 # 30 DTE: aún no
    nox = dfn.assess(inp(underlying=95.0, current=5.05, dte=4, expiry=TODAY + timedelta(days=4), ex_dividend_in=2), CFG)
    assert any("Extrínseco casi cero" in t and "ex-dividendo es en 2 días" in t and "asignación anticipada" in t for _, t in nox.steps)
    otm = dfn.assess(inp(underlying=103.5, current=priced(103.5), ex_dividend_in=2), CFG)
    assert "Extrínseco casi cero" not in texts(otm) and "Ex-dividendo" not in texts(otm)                         # fuera del dinero no hay riesgo de ejercicio


def test_losses_ivr_and_earnings():
    loss = dfn.assess(inp(underlying=99.0, entry=1.0, current=3.2), CFG)       # pierde 2,2 veces el crédito
    assert "2.2 veces" in texts(loss) and "valora cerrar" in texts(loss)
    assert "aceptar la asignación" in texts(dfn.assess(inp(underlying=99.0, entry=1.0, current=4.5), CFG))
    assert "mejor momento para rolar" in texts(dfn.assess(inp(underlying=98.0, current=priced(98.0), iv_rank=55.0), CFG))
    assert "rolar pagará poco" in texts(dfn.assess(inp(underlying=98.0, current=priced(98.0), iv_rank=12.0), CFG))
    near = dfn.assess(inp(underlying=98.0, current=priced(98.0), next_earnings=TODAY + timedelta(days=6)), CFG)
    assert "evento binario" in texts(near) and "put de protección" in texts(near)
    far = dfn.assess(inp(underlying=98.0, current=priced(98.0), next_earnings=TODAY + timedelta(days=25)), CFG)
    assert "evita meterlos en el nuevo vencimiento" in texts(far)
    beyond = dfn.assess(inp(underlying=98.0, current=priced(98.0), next_earnings=TODAY + timedelta(days=40)), CFG)
    assert "Resultados" not in texts(beyond)


# ---- rolls ---------------------------------------------------------------------------------------------------
def rq(days, strike, bid, delta=0.35):
    return RollQuote(TODAY + timedelta(days=days), strike, bid, bid + 0.1, delta)


def test_roll_picks_the_lowest_strike_that_keeps_the_minimum_credit():
    # cerrar cuesta 3,00; se cobró 2,00 → crédito mínimo = max(0,05; 10 % de 2,00 = 0,20)
    quotes = [rq(45, 100.0, 4.50, 0.52), rq(45, 97.5, 3.90, 0.45), rq(45, 95.0, 3.40, 0.38), rq(45, 92.5, 2.90, 0.31)]
    r = dfn.rank_rolls(100.0, 2.0, 3.0, quotes, TODAY, CFG)
    assert r.verdict == "roll" and len(r.best) == 1
    best = r.best[0]
    assert (best.strike, best.ok) == (95.0, True) and best.net_credit == pytest.approx(0.40) and best.credit_pct == pytest.approx(20.0)
    assert "95" in r.text and "0.40" in r.text


def test_roll_never_accepts_a_net_debit_and_flags_a_high_delta():
    r = dfn.rank_rolls(100.0, 2.0, 3.0, [rq(45, 95.0, 2.95, 0.38), rq(45, 90.0, 2.0, 0.30)], TODAY, CFG)
    assert r.verdict == "none" and not any(o.ok for o in r.best) and "no es viable" in r.text and "acepta la asignación" in r.text
    d = dfn.rank_rolls(100.0, 2.0, 3.0, [rq(45, 100.0, 3.8, 0.55), rq(45, 98.0, 3.7, 0.52)], TODAY, CFG)
    assert d.best[0].ok and any("solo compras tiempo" in n for n in d.best[0].notes)           # Δ > 0,50: solo se compra tiempo


def test_roll_extends_the_term_when_the_normal_range_has_no_credit():
    r = dfn.rank_rolls(100.0, 2.0, 3.0, [rq(45, 95.0, 2.9, 0.38), rq(80, 95.0, 3.6, 0.36)], TODAY, CFG)
    assert r.verdict == "extend" and "alargando el plazo" in r.text and r.best[1].dte == 80
    assert any("más allá de 60" in n for n in r.best[1].notes)


def test_roll_ignores_higher_strikes_short_or_far_expiries_and_dead_quotes():
    quotes = [rq(45, 105.0, 6.0), rq(10, 95.0, 4.0), rq(120, 95.0, 5.0), RollQuote(TODAY + timedelta(days=45), 95.0, None, None), rq(45, 95.0, 0.0)]
    r = dfn.rank_rolls(100.0, 2.0, 3.0, quotes, TODAY, CFG)
    assert r.best == () and r.verdict == "none"


# ---- integración con el estado de la put y con las reglas del perfil ------------------------------------------
def test_put_status_adds_the_defense_for_profiles_that_use_it_only():
    mcfg = ManagementSettings(default_profile="tastytrade")
    pos = short_put(strike=100.0, days=30, avg_cost=200.0, market_value=-priced(98.0) * 100 * 2)
    tasty = put_status(pos, TODAY, 98.0, mcfg, "tastytrade")
    assert tasty.defense is not None and tasty.defense.zone == ATM and any("rolar abajo y fuera" in a.text for a in tasty.alerts)
    tpc = put_status(pos, TODAY, 98.0, mcfg, "tpc")
    assert tpc.defense is None and not any("rolar abajo y fuera" in a.text for a in tpc.alerts) and any("no se rola" in a.text for a in tpc.alerts)
    off = put_status(pos, TODAY, 98.0, mcfg, "tastytrade", rules=exits.resolve("tastytrade", {"defense": False}, mcfg))
    assert off.defense is None
    assert put_status(replace(pos, hedge_strike=95.0), TODAY, 98.0, mcfg, "tastytrade").defense is None       # los spreads no se defienden así


def test_defense_rule_is_per_style_and_survives_overrides():
    mcfg = ManagementSettings()
    assert exits.defaults("tastytrade", mcfg).defense is True and exits.defaults("tpc", mcfg).defense is False
    assert exits.resolve("tpc", {"defense": True}, mcfg).defense is True
    assert exits.check_overrides({"defense": True, "pin_dte": 3}) == {"defense": True, "pin_dte": 3} and exits.check_overrides({"defense": "yes"}) == {}
    assert exits.overrides_of("tpc", exits.resolve("tpc", {"defense": True}, mcfg), mcfg) == {"defense": True}
    assert any("Defensa de las puts desnudas" in t for t in exits.describe(exits.defaults("tastytrade", mcfg)))
    assert any("Sin avisos de defensa" in t for t in exits.describe(exits.defaults("tpc", mcfg)))


def test_settings_validate_the_bands():
    for bad in ({"tested_delta": 0.6}, {"roll_dte_min": 70}, {"loss_multiple": 4}, {"call_delta_min": 0.5}, {"protect_strike_pct_min": 20}):
        with pytest.raises(ValueError):
            DefenseSettings(**bad)
