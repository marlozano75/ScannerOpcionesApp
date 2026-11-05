from datetime import date, timedelta

from scanner_opciones.scanner import yield_history as yh


def test_third_friday_and_the_last_expired_monthly_expiries():
    assert third(2026, 10) == date(2026, 10, 16) and third(2026, 8) == date(2026, 8, 21)
    assert yh.past_monthly_expiries(date(2026, 11, 5), 3) == [date(2026, 8, 21), date(2026, 9, 18), date(2026, 10, 16)]
    # el mismo día del vencimiento aún no ha vencido; al día siguiente sí
    assert yh.past_monthly_expiries(date(2026, 10, 16), 1) == [date(2026, 9, 18)]
    assert yh.past_monthly_expiries(date(2026, 10, 17), 1) == [date(2026, 10, 16)]
    assert yh.past_monthly_expiries(date(2026, 2, 3), 2) == [date(2025, 12, 19), date(2026, 1, 16)]      # cruza el año


def third(y, m):
    return yh.third_friday(y, m)


def test_reference_day_is_the_last_close_not_after_expiry_minus_dte():
    days = [date(2026, 9, d) for d in (14, 15, 16, 17, 18)]
    assert yh.reference_day(date(2026, 10, 16), 30, days) == date(2026, 9, 16)
    assert yh.reference_day(date(2026, 10, 16), 30, [date(2026, 9, 17)]) is None


def test_candidate_strikes_are_on_the_grid_at_or_below_the_target():
    assert yh.candidate_strikes(100.0, 5.0) == [95.0]                          # objetivo 95: cae en la rejilla de los tres pasos
    assert yh.candidate_strikes(103.0, 5.0) == [97.5, 97.0, 95.0]              # objetivo 97,85: pasos de 1, 2,5 y 5 (precio < 200)
    assert yh.candidate_strikes(300.0, 5.0) == [285.0, 280.0]                  # objetivo 285: pasos de 5 y 10
    assert all(k <= 103 * 0.95 for k in yh.candidate_strikes(103.0, 5.0))
    assert yh.candidate_strikes(10.0, 10.0) == [9.0, 7.5]                 # objetivo 9: pasos de 0,5, 1 y 2,5


def test_option_symbol_and_root():
    assert yh.option_symbol("AAPL", date(2026, 10, 16), 250.0) == ".AAPL261016P250"
    assert yh.option_symbol("F", date(2026, 8, 21), 12.5) == ".F260821P12.5"
    assert yh.dx_root("aapl") == "AAPL" and yh.dx_root("BRK-B") is None and yh.dx_root("BRK.B") is None and yh.dx_root("PBR A") is None


def test_pick_bid_falls_back_a_few_days_and_ignores_empty_bids():
    ref = date(2026, 9, 16)
    assert yh.pick_bid({ref: 1.2}, ref) == 1.2
    assert yh.pick_bid({ref - timedelta(days=2): 0.8}, ref) == 0.8
    assert yh.pick_bid({ref - timedelta(days=5): 0.8}, ref) is None
    assert yh.pick_bid({ref: 0.0}, ref) is None


def month(best, pending=False):
    return yh.Month(date(2026, 9, 18), date(2026, 8, 19), 100.0, best, pending)


def test_judge_discards_only_with_enough_months_with_data_and_too_few_hits():
    assert yh.judge("A", [month(0.4), month(0.3), month(0.2)], 1.0, 2, 2).status == yh.DISCARD
    assert yh.judge("A", [month(1.5), month(0.3), month(1.2)], 1.0, 2, 2).status == yh.OK
    assert yh.judge("A", [month(1.5), month(0.3), month(0.2)], 1.0, 2, 2).status == yh.DISCARD         # solo un acierto de los 2 exigidos
    assert yh.judge("A", [month(None), month(0.3), month(None)], 1.0, 2, 2).status == yh.NO_DATA        # un solo mes con bid: no se decide
    assert yh.judge("A", [month(None), month(None)], 1.0, 1, 1).status == yh.NO_DATA
    assert yh.judge("A", [month(0.3), month(0.3, pending=True)], 1.0, 2, 2).status == yh.PENDING        # falta pedir contratos
    v = yh.judge("A", [month(0.4), month(0.9)], 1.0, 1, 1)
    assert v.hits == 0 and v.with_data == 2 and v.best_pct == 0.9
