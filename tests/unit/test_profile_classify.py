"""Clasificación de una put vendida en un perfil del Scanner según el DTE con el que se abrió."""
from scanner_opciones.portfolio.classify import classify_dte, dte_range
from scanner_opciones.scanner.profiles import UNASSIGNED, Profile


def prof(name, lo, hi, instruments=("put",)):
    params = {"dte_min": str(lo), "dte_max": str(hi)}
    return Profile(name, instruments, put=params, spread=params)


PROFILES = [prof("Mensual", 25, 35), prof("Táctico", 10, 14), prof("Tastytrade", 30, 45)]


def test_the_narrowest_range_wins_and_what_fits_nowhere_is_left_without_profile():
    assert classify_dte(12, PROFILES) == "Táctico"
    assert classify_dte(32, PROFILES) == "Mensual"           # 25–35 y 30–45 lo contienen: gana el rango más estrecho
    assert classify_dte(40, PROFILES) == "Tastytrade"
    assert classify_dte(20, PROFILES) == UNASSIGNED          # ningún rango
    assert classify_dte(5, PROFILES) == UNASSIGNED           # por debajo de todos los rangos


def test_the_range_of_a_profile_is_the_union_of_its_active_instruments():
    p = Profile("X", ("put", "spread"), {"dte_min": "20", "dte_max": "30"}, {"dte_min": "25", "dte_max": "45"})
    assert dte_range(p) == (20, 45)
    assert dte_range(Profile("Y", ("put",), {"dte_min": "20", "dte_max": "30"}, {"dte_min": "1", "dte_max": "99"})) == (20, 30)
    assert dte_range(Profile("Z", ("put",), {}, {})) is None
