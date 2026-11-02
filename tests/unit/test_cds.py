from datetime import date, timedelta

from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionChain, OptionContract, TickerInfo
from scanner_opciones.scanner.candidates import call_candidate_contracts
from scanner_opciones.scanner.cds import build_call_debit_spreads

TODAY = date(2026, 11, 2)
EXP = TODAY + timedelta(days=300)


def call(strike, bid, ask, delta, oi=100, iv=0.5, expiry=EXP):
    c = OptionContract("ZZZ", expiry, strike, OptionRight.CALL, 100)
    return ContractSnapshot(c, None, bid=bid, ask=ask, delta=delta, iv=iv, open_interest=oi)


def info(price=100.0, iv30=50.0):
    return {"ZZZ": TickerInfo("ZZZ", underlying_price=price, iv30=iv30)}


# sin SMA200 ni mínimo de retorno máximo: aísla cada prueba
def cfg(**kw):
    base = dict(sma200_below_pct_min=None, min_max_return_pct=None, width_options=(10.0,), min_debit_usd=0, max_debit_usd=10000)
    base.update(kw)
    return CdsSettings(**base)


def test_required_move_filter_is_mandatory():
    # comprada 130 (30 % OTM) ask 4,00 delta 0,30; vendida 140 bid 2,20 ask 2,40 delta 0,22: débito 4 − 2,25 = 1,75; delta neta 0,08
    # necesario = 0,2 × 1,75 / 0,08 = 4,375 $ (4,4 %); esperado a 10 días = 0,5 × √(10/365) = 8,3 % → 0,53×
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    [r] = build_call_debit_spreads(snaps, info(), cfg(), TODAY)
    assert round(r.debit, 2) == 1.75 and round(r.net_delta, 2) == 0.08
    assert round(r.required_move_pct, 1) == 4.4 and round(r.expected_move_pct, 1) == 8.3
    assert r.required_move_mult < 1
    # con IV baja el mismo spread exige más de lo que cabe esperar: se descarta
    assert build_call_debit_spreads(snaps, info(iv30=20.0), cfg(), TODAY) == []


def test_net_delta_not_positive_is_discarded():
    assert build_call_debit_spreads([call(130, 3.9, 4.0, 0.25), call(140, 2.2, 2.4, 0.25)], info(), cfg(), TODAY) == []


def test_dte_and_otm_window_and_missing_data():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    assert build_call_debit_spreads(snaps, info(price=120.0), cfg(), TODAY) == []         # 130 es solo 8 % OTM
    assert build_call_debit_spreads(snaps, info(), cfg(dte_min=350, dte_max=400), TODAY) == []
    assert build_call_debit_spreads(snaps, {}, cfg(), TODAY) == []                       # sin precio


def test_leg_liquidity_and_debit_limits():
    assert build_call_debit_spreads([call(130, 3.9, 4.0, 0.30, oi=1), call(140, 2.2, 2.4, 0.22)], info(), cfg(), TODAY) == []
    assert build_call_debit_spreads([call(130, 0, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)], info(), cfg(), TODAY) == []
    assert build_call_debit_spreads([call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)], info(), cfg(max_debit_usd=100), TODAY) == []


def test_sma200_pullback_filter():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    below = {"ZZZ": [(TODAY - timedelta(days=i), 120.0) for i in range(250)]}          # SMA200 = 120: precio 100 está 16,7 % por debajo
    above = {"ZZZ": [(TODAY - timedelta(days=i), 105.0) for i in range(250)]}          # solo 4,8 % por debajo
    c = cfg(sma200_below_pct_min=10)
    assert len(build_call_debit_spreads(snaps, info(), c, TODAY, bars=below)) == 1
    assert build_call_debit_spreads(snaps, info(), c, TODAY, bars=above) == []
    assert build_call_debit_spreads(snaps, info(), c, TODAY, bars={}) == []


def test_call_candidates_add_short_legs():
    chain = OptionChain("ZZZ", frozenset({EXP, TODAY + timedelta(days=30)}), frozenset({100.0, 125.0, 130.0, 140.0, 150.0, 400.0}), 100)
    got = {(c.strike, c.right) for c in call_candidate_contracts(chain, 100.0, TODAY, CdsSettings(width_options=(10.0,)))}
    # compradas 125 y 130 y 150 (25–50 % OTM) y sus vendidas +10 que existen (140, 150); el vencimiento a 30 días y el 400 quedan fuera
    assert got == {(125.0, OptionRight.CALL), (130.0, OptionRight.CALL), (140.0, OptionRight.CALL), (150.0, OptionRight.CALL)}


def test_sma200_distance_is_always_computed_but_only_filters_when_set():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    above = {"ZZZ": [(TODAY - timedelta(days=i), 105.0) for i in range(250)]}          # precio 100: 4,8 % por debajo
    [r] = build_call_debit_spreads(snaps, info(), cfg(), TODAY, bars=above)            # filtro apagado: sale, con la columna rellena
    assert round(r.sma200_distance_pct, 1) == -4.8
    [r] = build_call_debit_spreads(snaps, info(), cfg(), TODAY, bars={})               # sin cierres: sale y la columna queda vacía
    assert r.sma200_distance_pct is None


def test_width_percent_window_and_best_width():
    # comprada 130 con dos vendidas posibles: 140 (ancho 10 = 7,7 %) y 150 (ancho 20 = 15,4 %)
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22), call(150, 1.2, 1.4, 0.15)]
    assert {r.width for r in build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0)), TODAY)} == {10.0, 20.0}
    assert {r.width for r in build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0), width_pct_min=10), TODAY)} == {20.0}
    assert {r.width for r in build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0), width_pct_max=10), TODAY)} == {10.0}
    every = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0)), TODAY)
    best = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0), best_width=True), TODAY)
    # una fila por call comprada (130 y 140): la de menor movimiento necesario de sus anchos
    assert sorted(r.long.contract.strike for r in best) == [130, 140] and len(every) == 3
    for r in best:
        assert r.required_move_mult == min(x.required_move_mult for x in every if x.long.contract.strike == r.long.contract.strike)
