from scanner_opciones.config.settings import SelectionSettings
from scanner_opciones.scanner.selection import ACTIVE, QUARANTINE, WARM, ProfileTarget, day_ratio, evaluate

RULES = SelectionSettings(enabled=True)      # ventana 10, mínimo 5 sesiones, 3 aciertos, templado desde el 60 %
TARGETS = {"TPC": ProfileTarget(1.0, 30), "Tactico": ProfileTarget(1.0, 12), "CDS": ProfileTarget(1.0, 1.0)}


def row(profile="TPC", contracts=3, closable=1, best=None, best_closable=None, wide=None):
    return {"profile": profile, "contracts": contracts, "closable": closable, "best_yield_pct": best,
            "best_closable_yield_pct": best_closable, "best_ann_wide_pct": wide}


def test_closable_premium_counts_in_full_and_an_unclosable_one_never_reaches_one():
    assert day_ratio([row(best=1.5, best_closable=1.2)], TARGETS) == 1.2
    assert day_ratio([row(best=2.0, best_closable=None)], TARGETS) == 0.8              # prima de sobra pero ningún contrato cerrable
    assert day_ratio([row(best=0.5, best_closable=None)], TARGETS) == 0.4


def test_without_contracts_in_the_window_the_wide_band_is_used():
    # exigido anualizado = 1 % × 365 / 30 = 12,17 %; la banda ancha da 6,08 % → mitad, × 0,8
    assert abs(day_ratio([row(contracts=0, closable=0, wide=6.0833)], TARGETS) - 0.4) < 1e-3
    assert day_ratio([row(contracts=0, closable=0)], TARGETS) is None                     # sin ningún dato: no es una medida


def test_best_allowed_profile_wins_and_unknown_profiles_are_ignored():
    rows = [row("TPC", best_closable=0.3), row("Tactico", best_closable=1.4), row("Otro", best_closable=9.0)]
    assert day_ratio(rows, TARGETS) == 1.4
    assert day_ratio(rows, TARGETS, allowed={"TPC"}) == 0.3
    assert day_ratio(rows, TARGETS, allowed={"Otro"}) is None


def test_cds_row_needs_closable_calls():
    assert day_ratio([row("CDS", contracts=10, closable=2)], TARGETS) == 1.0
    assert day_ratio([row("CDS", contracts=10, closable=0)], TARGETS) == 0.5
    assert day_ratio([row("CDS", contracts=10, closable=2)], {"TPC": ProfileTarget(1.0, 30)}) is None   # CDS apagado


def test_not_enough_sessions_keeps_the_ticker_active():
    state, why = evaluate([0.0, 0.0, 0.0, 0.0], RULES)
    assert state == ACTIVE and "4 de 5" in why


def test_active_warm_and_quarantine_by_recurrence():
    assert evaluate([1.2, 0.2, 1.0, 0.1, 1.5, 0.0], RULES)[0] == ACTIVE                       # 3 aciertos
    assert evaluate([0.7, 0.9, 0.2, 0.65, 0.1, 1.2], RULES)[0] == WARM                       # 1 acierto y 3 casi (4 ≥ 60 %)
    assert evaluate([0.1, 0.2, 0.0, 0.3, 0.9, 0.1], RULES)[0] == QUARANTINE


def test_only_the_last_window_counts():
    old_hits = [1.0] * 5
    assert evaluate(old_hits + [0.0] * 10, RULES)[0] == QUARANTINE                           # los aciertos de hace más de 10 sesiones ya no cuentan


def test_hysteresis_keeps_an_active_ticker_with_one_hit_less():
    ratios = [1.0, 1.0, 0.2, 0.1, 0.0, 0.3]                                                   # 2 aciertos
    assert evaluate(ratios, RULES, previous=None)[0] != ACTIVE
    assert evaluate(ratios, RULES, previous=ACTIVE)[0] == ACTIVE
