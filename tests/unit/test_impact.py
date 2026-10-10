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
BASE = criteria_from_settings(Settings()).with_filters(min_bid=None, min_annual_yield_pct=0)
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
