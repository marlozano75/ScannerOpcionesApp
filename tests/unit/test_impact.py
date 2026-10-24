"""Cuánto descarta cada filtro: «solo este filtro deja» y «solo él descarta» sobre la ventana de contratos."""
from datetime import date, datetime, timedelta

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.impact import active_groups, impact_report

TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 12, 0)
# sin el bid mínimo por defecto: estos tests miden solo los filtros que ellos activan
BASE = criteria_from_settings(Settings()).with_filters(min_bid=None, min_yield_pct=0, floor_eps=False, floor_quarters=0, floor_fcf=False)
INFOS = {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}


def snap(strike, oi, spread):
    """Un put a 30 días con un yield que siempre pasa; solo varían OI y spread."""
    c = OptionContract("AAPL", TODAY + timedelta(days=30), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=1.0, ask=1.0, open_interest=oi, spread_pct=spread, yield_pct=1.2)


def report(snaps, **filters):
    return impact_report(snaps, INFOS, BASE.with_filters(**filters), TODAY)


def by_key(r):
    return {f.key: f for f in r.filters}


def test_alone_and_exclusive_count_what_each_filter_does_by_itself_and_on_top_of_the_others():
    snaps = [snap(80, 500, 5), snap(81, 50, 5), snap(82, 500, 50), snap(83, 50, 50)]     # A ok · B poco OI · C spread alto · D ambos
    r = report(snaps, min_oi=100, max_spread_pct=35)
    assert (r.window, r.final) == (4, 1)
    f = by_key(r)
    assert (f["oi"].alone, f["oi"].exclusive) == (2, 1)           # sola deja A y C; quitarla recupera solo B
    assert (f["spread"].alone, f["spread"].exclusive) == (2, 1)   # sola deja A y B; quitarla recupera solo C


def test_a_filter_that_adds_nothing_over_the_others_is_redundant():
    snaps = [snap(80, 500, 5), snap(83, 50, 50)]                  # lo que falla el OI también falla el spread
    r = report(snaps, min_oi=100, max_spread_pct=35)
    f = by_key(r)
    assert (f["oi"].alone, f["oi"].exclusive) == (1, 0) and (f["spread"].alone, f["spread"].exclusive) == (1, 0)


def test_most_restrictive_filters_come_first_and_only_active_ones_are_listed():
    snaps = [snap(80, 500, 5), snap(81, 50, 5), snap(82, 50, 5), snap(83, 50, 50)]
    r = report(snaps, min_oi=100, max_spread_pct=35)
    assert [f.key for f in r.filters] == ["oi", "spread"]         # OI deja 1; spread deja 3
    assert [g[0] for g in active_groups(BASE)] == []              # sin filtros activos no hay nada que medir
    assert report(snaps).filters == () and report(snaps).window == 4


def test_the_window_ignores_contracts_outside_the_strike_range():
    near = snap(95, 500, 5)                                       # solo un 5 % por debajo: fuera del 10–30 %
    r = report([near, snap(80, 500, 5)], min_oi=100)
    assert r.window == 1


def test_nothing_passing_is_reported_with_zero_exclusive_for_every_filter():
    snaps = [snap(80, 50, 50)]
    r = report(snaps, min_oi=100, max_spread_pct=35)
    assert r.final == 0 and all(f.exclusive == 0 for f in r.filters)


def test_each_filter_shows_its_current_configuration():
    from scanner_opciones.scanner.impact import describe_group
    c = criteria_from_settings(Settings()).with_filters(
        min_iv_rank=50.0, min_abs_delta=None, max_abs_delta=0.3, min_oi=10, ma200="above", cmp_ema9_ema20="gte",
        max_debt_to_equity=1.0, signals_required=2, rsi_rebound_max=35.0, min_iv_hv_ratio=1.2)
    assert describe_group("iv_rank", c) == "≥ 50"
    assert describe_group("delta", c) == "|Δ| ≤ 0,3"
    assert describe_group("oi", c) == "≥ 10"
    assert describe_group("averages", c) == "MA200 precio sobre; EMA9/EMA20 ≥ (velas daily)"
    assert describe_group("max_debt_to_equity", c) == "deuda/patrimonio ≤ 1"
    assert describe_group("signals", c) == "al menos 2 de: RSI rebotando desde el nivel de sobreventa tocó ≤ 35 y sube"


def test_iv_rank_o_percentile_es_un_solo_grupo():
    from scanner_opciones.scanner.impact import active_groups
    c = BASE.with_filters(min_iv_rank=30, min_iv_percentile=50, iv_any=True)
    assert [g[0] for g in active_groups(c)] == ["iv_either"]       # quitar solo uno haría el filtro más estricto: impacto negativo
    assert [g[0] for g in active_groups(c.with_filters(iv_any=False))] == ["iv_rank", "iv_pct"]


def test_the_minimum_put_quality_is_counted_like_any_other_filter():
    infos = {"AAPL": TickerInfo("AAPL", underlying_price=100.0, eps_ttm=2.0, positive_quarters=4, fcf_ttm=1.0),
             "LOSS": TickerInfo("LOSS", underlying_price=100.0, eps_ttm=-1.0, positive_quarters=1, fcf_ttm=-1.0)}

    def other(ticker, strike):
        c = OptionContract(ticker, TODAY + timedelta(days=30), strike, OptionRight.PUT)
        return ContractSnapshot(c, NOW, bid=1.0, ask=1.0, open_interest=500, spread_pct=5, yield_pct=1.2)

    snaps = [snap(80, 500, 5), snap(81, 500, 5), other("LOSS", 80)]
    crit = BASE.with_filters(floor_eps=True, floor_quarters=3, floor_fcf=True)
    r = impact_report(snaps, infos, crit, TODAY)
    f = by_key(r)
    assert (r.window, r.final) == (3, 2) and set(f) == {"floor_eps", "floor_quarters", "floor_fcf"}
    assert f["floor_eps"].alone == 2 and f["floor_eps"].exclusive == 0         # los otros dos la cubren: LOSS falla las tres
    assert "solo spread" in f["floor_eps"].config and f["floor_quarters"].config.startswith("≥ 3")
    assert impact_report(snaps, infos, BASE, TODAY).window == 3 and impact_report(snaps, infos, BASE, TODAY).filters == ()
