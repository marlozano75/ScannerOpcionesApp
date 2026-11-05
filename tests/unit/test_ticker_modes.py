from types import SimpleNamespace

from scanner_opciones.config.settings import TickerModesSettings
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.modes import ALL_ON, derive_put_price_cap, evaluate

RULES = TickerModesSettings(enabled=True)


def info(price=100.0, ivr=40.0, ivp=60.0, liq=3):
    return TickerInfo("ZZZ", underlying_price=price, iv_rank=ivr, iv_percentile=ivp, option_liquidity=liq)


def crit(risk, discount):
    return SimpleNamespace(max_capital_risk_pct=risk, strike_below_pct_min=discount)


def test_all_modes_on_with_good_data():
    m = evaluate(info(), RULES, 120.0)
    assert (m.put, m.put_spread, m.cds) == (True, True, True) and m.reasons == {}


def test_high_price_only_turns_off_naked_puts():
    m = evaluate(info(price=300), RULES, 115.0)
    assert (m.put, m.put_spread, m.cds) == (False, True, True) and "115" in m.reasons["put"]


def test_liquidity_applies_to_both_spreads_and_missing_data_turns_off():
    m = evaluate(info(liq=2), RULES, 120.0)              # recompra fácil: puts desde 2, spreads y CDS desde 3
    assert (m.put, m.put_spread, m.cds) == (True, False, False) and "liquidez" in m.reasons["put_spread"]
    m = evaluate(info(liq=1), RULES, 120.0)
    assert (m.put, m.put_spread, m.cds) == (False, False, False) and "recomprar" in m.reasons["put"]
    m = evaluate(info(liq=None), RULES, 120.0)           # sin dato: apaga spreads y CDS, no las puts desnudas
    assert m.put and not m.put_spread and not m.cds and "sin dato" in m.reasons["cds"]


def test_iv_is_either_rank_or_percentile_and_only_for_put_spreads_by_default():
    m = evaluate(info(ivr=10, ivp=60), RULES, 120.0)       # basta el percentil
    assert m.put_spread
    m = evaluate(info(ivr=10, ivp=20), RULES, 120.0)
    assert not m.put_spread and "IV baja" in m.reasons["put_spread"]
    assert m.cds                                            # el CDS no pide IV por defecto
    m = evaluate(info(ivr=None, ivp=None), RULES, 120.0)
    assert not m.put_spread and "sin IV" in m.reasons["put_spread"] and m.cds


def test_cds_iv_minimum_is_optional():
    rules = TickerModesSettings(enabled=True, cds_min_iv_percentile=30)
    assert not evaluate(info(ivr=5, ivp=10), rules, 120.0).cds


def test_iv_hysteresis_keeps_an_already_on_mode():
    low = info(ivr=26, ivp=46)                              # bajo los mínimos (30 / 50) pero dentro de los 5 puntos de holgura
    assert not evaluate(low, RULES, 120.0).put_spread
    previous = evaluate(info(), RULES, 120.0)
    assert evaluate(low, RULES, 120.0, previous).put_spread
    assert not evaluate(info(ivr=20, ivp=40), RULES, 120.0, previous).put_spread


def test_no_price_turns_everything_off_and_fallback_cap_is_used():
    assert not evaluate(None, RULES, 120.0).any and not evaluate(TickerInfo("ZZZ"), RULES, 120.0).any
    assert evaluate(info(price=110), RULES, None).put and not evaluate(info(price=130), RULES, None).put   # respaldo = 120
    assert ALL_ON.any_puts and ALL_ON.any


def test_put_price_cap_uses_the_least_restrictive_profile_with_a_limit():
    nlv = 66_000.0
    # Táctico: 15 % con descuento 10 %; TastyPut: 12 % con descuento 5 %; Mensual: sin límite (no cuenta)
    cap = derive_put_price_cap([crit(15, 10), crit(12, 5), crit(None, 20)], nlv, TickerModesSettings(put_price_margin_pct=0))
    assert round(cap, 1) == round(0.15 * nlv / (100 * 0.90), 1)
    assert round(derive_put_price_cap([crit(15, 10)], nlv, TickerModesSettings(put_price_margin_pct=5)), 1) == round(0.15 * nlv / 90 * 1.05, 1)
    assert derive_put_price_cap([crit(None, 10)], nlv, RULES) is None          # ningún perfil limita el capital
    assert derive_put_price_cap([crit(15, 10)], None, RULES) is None           # sin NLV
    assert derive_put_price_cap([crit(15, 10)], nlv, TickerModesSettings(put_max_price=90)) == 90   # precio fijo manda
