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


# ---- registro de escaneos: solo compiten los perfiles que mostraban el contrato ------------------------------------
from datetime import date

from scanner_opciones.portfolio.entry_fit import recognize_shown
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.scan_log import ScanLogRepo

DAY = date(2026, 10, 13)


def test_a_single_profile_in_the_log_decides_even_if_the_filters_would_say_otherwise():
    fit = recognize_shown(put(38, 25.0), PROFILES, ["TastyPut"], DAY)           # por filtros ganaría otro; el registro dice que solo salía en TastyPut
    assert fit.profile == "TastyPut" and "registro de escaneos" in fit.note and "13/10" in fit.note
    assert recognize_shown(put(38, 25.0), PROFILES, ["TastySpread"], DAY) is None      # perfil de otro instrumento: sin respuesta


def test_several_profiles_in_the_log_are_broken_by_filters_then_by_the_narrowest_dte():
    wide = Profile("Ancho", ("put",), {"discount": "5", "dte_min": "20", "dte_max": "60", "min_yield": "1"}, {})
    narrow = Profile("Estrecho", ("put",), {"discount": "5", "dte_min": "30", "dte_max": "45", "min_yield": "1"}, {})
    profiles = [wide, narrow, MENSUAL]
    both = recognize_shown(put(38, 8.0), profiles, ["Ancho", "Estrecho"], DAY)
    assert both.profile == "Estrecho" and both.exact and "desempate" in both.note       # cumplen todo las dos: gana el rango de DTE más estrecho
    mixed = recognize_shown(put(32, 8.0), profiles, ["Mensual (TPC)", "Ancho"], DAY)    # Mensual no cumple el descuento (20 %): los filtros mandan antes que el ancho
    assert mixed.profile == "Ancho" and mixed.exact


def test_the_fallback_profile_only_counts_when_it_is_the_only_one_shown():
    profiles = PROFILES
    assert recognize_shown(put(38, 8.0), profiles, [FALLBACK, "TastyPut"], DAY).profile == "TastyPut"
    assert recognize_shown(put(38, 8.0), profiles, [FALLBACK], DAY).profile == FALLBACK


def test_profiles_showing_covers_the_day_of_the_opening_only():
    repo = ScanLogRepo(Database(":memory:"))
    with repo.db.conn:
        c = repo.db.conn
        cols = "(profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, ended_at, active)"
        c.execute(f"INSERT INTO scan_log {cols} VALUES ('A','put','FRO','2026-11-20',49.2,NULL,'2026-10-12T10:00:00','2026-10-13T11:00:00','2026-10-13T11:15:00',0)")
        c.execute(f"INSERT INTO scan_log {cols} VALUES ('B','put','FRO','2026-11-20',49.2,NULL,'2026-10-14T10:00:00','2026-10-14T10:00:00',NULL,1)")
        c.execute(f"INSERT INTO scan_log {cols} VALUES ('S','spread','FRO','2026-11-20',49.2,45.0,'2026-10-13T10:00:00','2026-10-13T10:00:00',NULL,1)")
    args = ("put", "FRO", date(2026, 11, 20), 49.2, None)
    assert repo.profiles_showing(*args, DAY) == ["A"]
    assert repo.profiles_showing(*args, date(2026, 10, 14)) == ["B"]
    assert repo.profiles_showing(*args, date(2026, 10, 11)) == []              # el registro aún no existía
    assert repo.profiles_showing("spread", "FRO", date(2026, 11, 20), 49.2, 45.0, DAY) == ["S"]
