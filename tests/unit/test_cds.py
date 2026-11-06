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
    got = {(c.strike, c.right) for c in call_candidate_contracts(chain, 100.0, TODAY, CdsSettings(width_options=(10.0,), monthly_only=False))}
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
    every = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0), top_per_ticker=0), TODAY)
    best = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0, 20.0), best_width=True, top_per_ticker=0), TODAY)
    # una fila por call comprada (130 y 140): la de mayor puntuación de sus anchos
    assert sorted(r.long.contract.strike for r in best) == [130, 140] and len(every) == 3
    for r in best:
        assert r.score == max(x.score for x in every if x.long.contract.strike == r.long.contract.strike)


def test_probability_score_ranking_and_top_per_ticker():
    # comprada 130 + vendida 140 (mult 0,53× → prob ≈ 60 %) y comprada 125 + vendida 135 (más cara, delta neta menor)
    snaps = [call(125, 5.9, 6.0, 0.36), call(130, 3.9, 4.0, 0.30), call(135, 2.9, 3.1, 0.26), call(140, 2.2, 2.4, 0.22)]
    every = build_call_debit_spreads(snaps, info(), cfg(min_prob_pct=None, top_per_ticker=0), TODAY)
    assert len(every) >= 2
    assert [r.score for r in every] == sorted((r.score for r in every), reverse=True)
    assert all(abs(r.score - r.prob_pct * r.exit_return_pct / 100) < 1e-9 for r in every)
    assert all(0 < r.prob_pct <= 100 for r in every)
    # la probabilidad solo depende del múltiplo (menos movimiento necesario = más probable)
    assert all((a.required_move_mult <= b.required_move_mult) == (a.prob_pct >= b.prob_pct) for a in every for b in every)
    # tope por ticker: se quedan los de mayor puntuación
    one = build_call_debit_spreads(snaps, info(), cfg(min_prob_pct=None, top_per_ticker=1), TODAY)
    assert one == every[:1]
    # probabilidad mínima: por encima de la mejor no queda nada
    assert build_call_debit_spreads(snaps, info(), cfg(min_prob_pct=max(r.prob_pct for r in every) + 1, top_per_ticker=0), TODAY) == []


def test_exit_values_at_target_with_close_commission():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    [r] = build_call_debit_spreads(snaps, info(), cfg(target_gain_pct=20), TODAY, cost_ps=lambda m: 0.01)
    assert abs(r.exit_value - r.debit * 1.2) < 1e-9
    assert abs(r.exit_credit_usd - (r.debit * 1.2 - 0.01) * 100) < 1e-6
    assert abs(r.exit_profit_usd - (r.debit * 0.2 - 0.01) * 100) < 1e-6
    assert abs(r.exit_return_pct - (r.debit * 0.2 - 0.01) / r.debit * 100) < 1e-6
    # un objetivo que supera el ancho no se puede cobrar: se descarta
    assert build_call_debit_spreads(snaps, info(), cfg(target_gain_pct=500, max_required_move_mult=100, min_prob_pct=None), TODAY) == []


def test_min_exit_profit_filter():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]
    [r] = build_call_debit_spreads(snaps, info(), cfg(), TODAY)
    assert build_call_debit_spreads(snaps, info(), cfg(min_exit_profit_usd=r.exit_profit_usd - 1), TODAY) == [r]
    assert build_call_debit_spreads(snaps, info(), cfg(min_exit_profit_usd=r.exit_profit_usd + 1), TODAY) == []


def test_iv_at_horizon_interpolates_total_variance():
    from scanner_opciones.scanner.cds import iv_at_horizon
    t = lambda n: (TODAY + timedelta(days=n)).isoformat()
    i = TickerInfo("ZZZ", underlying_price=100.0, iv30=50.0, expiry_ivs={t(5): 0.80, t(25): 0.40, t(60): 0.30})
    assert abs(iv_at_horizon(i, 5, TODAY) - 0.80) < 1e-9
    mid = iv_at_horizon(i, 15, TODAY)                      # entre 5 y 25 días: varianza total lineal
    assert abs(mid - ((0.8**2 * 5 + 0.4**2 * 25) / 2 / 15) ** 0.5) < 1e-9 and 0.4 < mid < 0.8
    assert iv_at_horizon(i, 90, TODAY) == 0.30             # más allá del último: el más cercano
    assert iv_at_horizon(i, 2, TODAY) == 0.80              # antes del primero: el primero
    assert iv_at_horizon(TickerInfo("ZZZ", iv30=50.0), 10, TODAY) == 0.5      # sin vencimientos: IV30
    assert iv_at_horizon(TickerInfo("ZZZ"), 10, TODAY) is None


def test_earnings_in_move_window_excluded():
    snaps = [call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)]

    def with_earn(days):
        return {"ZZZ": TickerInfo("ZZZ", underlying_price=100.0, iv30=50.0, next_earnings=TODAY + timedelta(days=days))}
    assert build_call_debit_spreads(snaps, with_earn(5), cfg(exclude_earnings=True), TODAY) == []
    assert build_call_debit_spreads(snaps, with_earn(10), cfg(exclude_earnings=True), TODAY) == []      # el último día de la ventana cuenta
    assert len(build_call_debit_spreads(snaps, with_earn(11), cfg(exclude_earnings=True), TODAY)) == 1
    assert len(build_call_debit_spreads(snaps, with_earn(-3), cfg(exclude_earnings=True), TODAY)) == 1  # ya pasados: no cuentan
    assert len(build_call_debit_spreads(snaps, with_earn(5), cfg(exclude_earnings=False), TODAY)) == 1


def test_each_leg_has_its_own_oi_and_spread_limits():
    ok = [call(130, 3.9, 4.0, 0.30, oi=100), call(140, 2.2, 2.4, 0.22, oi=100)]
    assert len(build_call_debit_spreads(ok, info(), cfg(), TODAY)) == 1
    # OI de la vendida por debajo del suyo: la comprada, con el suyo cumplido, no basta
    low_short = [call(130, 3.9, 4.0, 0.30, oi=100), call(140, 2.2, 2.4, 0.22, oi=5)]
    assert build_call_debit_spreads(low_short, info(), cfg(min_long_oi=50, min_short_oi=50), TODAY) == []
    assert len(build_call_debit_spreads(low_short, info(), cfg(min_long_oi=50, min_short_oi=5), TODAY)) == 1
    # lo mismo con el OI de la comprada
    low_long = [call(130, 3.9, 4.0, 0.30, oi=5), call(140, 2.2, 2.4, 0.22, oi=100)]
    assert build_call_debit_spreads(low_long, info(), cfg(min_long_oi=50, min_short_oi=5), TODAY) == []
    assert len(build_call_debit_spreads(low_long, info(), cfg(min_long_oi=5, min_short_oi=50), TODAY)) == 1
    # spread: la vendida (2,2/2,4 = 8,7 %) pasa con 10 y no con 5; la comprada (3,9/4,0 = 2,5 %) pasa con 5
    assert len(build_call_debit_spreads(ok, info(), cfg(max_long_spread_pct=5, max_short_spread_pct=10), TODAY)) == 1
    assert build_call_debit_spreads(ok, info(), cfg(max_long_spread_pct=5, max_short_spread_pct=5), TODAY) == []
    assert build_call_debit_spreads(ok, info(), cfg(max_long_spread_pct=2, max_short_spread_pct=10), TODAY) == []


def test_bid_and_ask_size_filters_apply_to_both_legs_and_missing_data_fails():
    from dataclasses import replace
    a, b = call(130, 3.9, 4.0, 0.30), call(140, 2.2, 2.4, 0.22)
    sized = [replace(a, bid_size=50, ask_size=40), replace(b, bid_size=30, ask_size=20)]
    assert len(build_call_debit_spreads(sized, info(), cfg(), TODAY)) == 1                       # sin filtro no importa el tamaño
    assert len(build_call_debit_spreads(sized, info(), cfg(min_bid_size=30, min_ask_size=20), TODAY)) == 1
    assert build_call_debit_spreads(sized, info(), cfg(min_bid_size=31), TODAY) == []            # la vendida tiene bid size 30
    assert build_call_debit_spreads(sized, info(), cfg(min_ask_size=21), TODAY) == []            # la vendida tiene ask size 20
    assert build_call_debit_spreads([a, b], info(), cfg(min_bid_size=1), TODAY) == []            # sin dato de tamaño no pasa


def test_all_widths_uses_any_saved_strike_above_the_long():
    # comprada 130; vendidas guardadas a 135, 140 y 150 (anchos 5, 10 y 20); la lista solo trae el 10
    snaps = [call(130, 3.9, 4.0, 0.30), call(135, 3.0, 3.2, 0.26), call(140, 2.2, 2.4, 0.22), call(150, 1.2, 1.4, 0.15)]
    listed = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0,), width_pct_min=None, top_per_ticker=0), TODAY)
    every = build_call_debit_spreads(snaps, info(), cfg(width_options=(10.0,), all_widths=True, width_pct_min=None, top_per_ticker=0), TODAY)
    assert {r.width for r in listed if r.long.contract.strike == 130} == {10.0}
    assert {r.width for r in every if r.long.contract.strike == 130} == {5.0, 10.0, 20.0}
    # el ancho / strike y el resto de filtros siguen mandando
    capped = build_call_debit_spreads(snaps, info(), cfg(all_widths=True, width_pct_min=7, top_per_ticker=0), TODAY)
    assert {r.width for r in capped if r.long.contract.strike == 130} == {10.0, 20.0}
