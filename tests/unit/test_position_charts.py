"""Gráfico por ticker del Panel: cierres diarios, strikes, vencimientos y la fecha de revisión (21 DTE) de las puts gestionadas con Tastytrade."""
from datetime import date, timedelta

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.portfolio.management import PutStatus, TASTYTRADE, TPC
from scanner_opciones.ui.position_charts import exit_plan, position_chart, position_mini

TODAY = date(2026, 10, 19)
CFG = ManagementSettings()


def put(strike=100.0, dte=35, profile=TASTYTRADE, captured=10.0, long_strike=None):
    return PutStatus("ABC", TODAY + timedelta(days=dte), strike, 2, dte, 2.0, 1.8, captured, 110.0, (), long_strike, profile)


def closes(n=50, start=100.0):
    return [(TODAY - timedelta(days=n - i), start + i * 0.2) for i in range(n)]


def test_tastytrade_put_gets_a_review_date_with_the_days_left():
    plan = exit_plan(put(dte=35), CFG, TODAY)
    assert plan.review_date == TODAY + timedelta(days=14) and plan.days_to_review == 14 and "faltan 14 días" in plan.text
    assert "zona de revisión" in exit_plan(put(dte=15), CFG, TODAY).text
    assert "recoger beneficios" in exit_plan(put(dte=35, captured=60.0), CFG, TODAY).text


def test_tpc_put_is_held_to_expiry_and_its_21_dte_date_is_only_a_reference():
    plan = exit_plan(put(profile=TPC), CFG, TODAY)
    assert plan.is_review is False and "mantener hasta el vencimiento" in plan.text
    assert plan.review_date == TODAY + timedelta(days=14) and "referencia a 21 DTE el" in plan.text          # la fecha se dibuja igual, solo como referencia


def test_chart_draws_price_strike_expiry_and_review_line():
    svg = str(position_chart(closes(), [put(strike=105.0, dte=35)], CFG, TODAY, 10, 110.0))
    assert svg.startswith("<svg") and "strike 105" in svg and "venc. " in svg and "revisar " in svg and "en 14 d" in svg
    assert svg.count('class="pt"') == len([1 for d, _ in closes() if d >= TODAY - timedelta(weeks=10)])


def test_spread_shows_the_bought_strike_and_a_tpc_put_shows_the_21_dte_reference():
    svg = str(position_chart(closes(), [put(strike=105.0, long_strike=100.0, profile=TPC)], CFG, TODAY, 10))
    assert "comprada 100" in svg and "venc. " in svg and "21 DTE " in svg and "revisar " not in svg          # TPC: referencia, no «revisar»


def test_a_far_expiry_is_marked_with_an_arrow_and_missing_closes_give_a_notice():
    svg = str(position_chart(closes(), [put(dte=250)], CFG, TODAY, 10))
    assert "→" in svg
    assert "en zona de revisión" in str(position_chart(closes(), [put(dte=15)], CFG, TODAY, 10))
    assert "Sin cierres" in str(position_chart([], [put()], CFG, TODAY))


def test_thumbnail_has_no_text_but_draws_strike_expiry_and_the_review_zone():
    svg = str(position_mini(closes(), [put(strike=105.0, long_strike=100.0)], CFG, TODAY, 10, 110.0))
    assert svg.startswith("<svg") and "<text" not in svg and "stroke-dasharray" in svg and 'opacity=".2"' in svg
    assert str(position_mini([], [put()], CFG, TODAY)) == ""


def test_earnings_date_is_drawn_as_a_vertical_line_with_its_days_left():
    svg = str(position_chart(closes(), [put(dte=35)], CFG, TODAY, 10, 110.0, TODAY + timedelta(days=12)))
    assert "resultados 31/10 · en 12 d" in svg and "Presentación de resultados" in svg
    assert "resultados" not in str(position_chart(closes(), [put(dte=35)], CFG, TODAY, 10, 110.0, None))
    assert "resultados" not in str(position_chart(closes(), [put(dte=35)], CFG, TODAY, 10, 110.0, TODAY - timedelta(days=1)))
