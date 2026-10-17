"""Reconocer con qué estrategia se abrió una posición nueva por los filtros que cumplía el día de la apertura."""
from scanner_opciones.portfolio.classify import FALLBACK
from scanner_opciones.portfolio.entry_fit import EntryFacts, EntryFit, recognize
from scanner_opciones.scanner.profiles import Profile

MENSUAL = Profile("Mensual (TPC)", ("put",), {"discount": "20", "dte_min": "25", "dte_max": "35", "min_yield": "1"}, {})
TACTICO = Profile("Táctico (TPC)", ("put",), {"discount": "10", "dte_min": "10", "dte_max": "14", "min_yield": "1"}, {})
TASTYPUT = Profile("TastyPut", ("put",), {"discount": "5", "dte_min": "30", "dte_max": "45", "min_yield": "1"}, {})
TASTYSPREAD = Profile("TastySpread", ("spread",), {}, {"discount": "5", "dte_min": "30", "dte_max": "45", "use_rr": "on", "rr": "25"})
PROFILES = [MENSUAL, TACTICO, TASTYPUT, TASTYSPREAD, Profile(FALLBACK, ("put", "spread"), {"dte_min": "1", "dte_max": "60"}, {"dte_min": "1", "dte_max": "60"})]


def put(dte, discount, yield_pct=1.5):
    return EntryFacts("put", dte, discount, yield_pct=yield_pct)


def test_where_dte_ranges_overlap_the_discount_tells_the_strategies_apart():
    deep = recognize(put(32, 25.0), PROFILES, FALLBACK)
    assert deep.profile == "Mensual (TPC)" and deep.exact                       # cumple las dos; gana la más exigente (descuento 20 %)
    shallow = recognize(put(32, 8.0), PROFILES, FALLBACK)
    assert shallow.profile == "TastyPut" and shallow.exact                      # el descuento de Mensual no se cumple: solo TastyPut
    assert recognize(put(40, 25.0), PROFILES, FALLBACK).profile == "TastyPut"   # 40 DTE no está en el rango de Mensual


def test_the_tactical_profile_is_recognised_by_its_short_dte():
    fit = recognize(put(12, 12.0), PROFILES, FALLBACK)
    assert fit.profile == "Táctico (TPC)" and fit.exact


def test_a_spread_is_matched_against_spread_profiles_only_and_uses_return_on_risk():
    ok = recognize(EntryFacts("spread", 35, 9.0, ror_pct=30.0), PROFILES, FALLBACK)
    assert ok.profile == "TastySpread" and ok.exact and "retorno/riesgo" in ok.note
    low = recognize(EntryFacts("spread", 35, 9.0, ror_pct=12.0), PROFILES, FALLBACK)
    assert low.profile == "TastySpread" and not low.exact and "aproximado" in low.note        # DTE encaja pero no el retorno


def test_when_nothing_is_met_the_closest_profile_with_the_right_dte_wins_and_otherwise_the_fallback():
    near = recognize(put(32, 3.0), PROFILES, FALLBACK)                           # 3 % de descuento: ninguno lo cumple del todo
    assert near.profile in ("Mensual (TPC)", "TastyPut") and not near.exact
    assert recognize(put(20, 30.0), PROFILES, FALLBACK).profile == FALLBACK     # 20 DTE no encaja en el rango de ningún perfil
    assert recognize(put(20, 30.0), PROFILES, FALLBACK).total == 0


def test_unknown_values_are_not_counted_and_do_not_discard_a_profile():
    fit = recognize(EntryFacts("put", 32, None, yield_pct=1.5), PROFILES, FALLBACK)
    assert fit.profile == "Mensual (TPC)" and fit.exact and "?" in fit.note       # sin cierre de ese día: el descuento no se mide


def test_entry_fit_roundtrips_through_json():
    fit = recognize(put(32, 25.0), PROFILES, FALLBACK)
    assert EntryFit.from_json(fit.to_json()) == fit
