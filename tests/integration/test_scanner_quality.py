"""Calidad de la empresa en el Scanner: bloque de solvencia, «deuda baja o manejable», combinaciones, recuentos por opción
y el panel «¿Cuánto descarta cada filtro?»."""
import json
import re

import pytest

from scanner_opciones.config.settings import QualityPreset, QualitySettings
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.quality import ticker_quality_reject
from tests.integration.test_web import BASE, client_and_service, refresh  # noqa: F401
from scanner_opciones.config.settings import Settings

CRIT = criteria_from_settings(Settings())
EXEMPT = Settings().scanner.quality.exempt_sectors


def reject(info, **filters):
    return ticker_quality_reject(info, CRIT.with_filters(**filters), EXEMPT)


# ---- deuda baja o manejable ---------------------------------------------------------------------------------
def test_manageable_debt_passes_low_debt_or_well_covered_debt_and_rejects_only_high_and_uncovered_debt():
    low = TickerInfo("A", debt_to_equity=0.3, interest_coverage=1.0)             # poca deuda aunque mal cubierta
    managed = TickerInfo("B", debt_to_equity=1.7, interest_coverage=16.0)         # mucha deuda pero muy bien cubierta (Paycom)
    bad = TickerInfo("C", debt_to_equity=1.7, interest_coverage=1.5)              # mucha deuda y mal cubierta
    nodata = TickerInfo("D")
    for ok in (low, managed):
        assert reject(ok, require_manageable_debt=True) is None
    assert "deuda alta y mal cubierta" in reject(bad, require_manageable_debt=True)
    assert reject(nodata, require_manageable_debt=True) is not None               # sin dato = no cumple
    assert reject(TickerInfo("E", debt_to_equity=0.9, interest_coverage=999.0), require_manageable_debt=True) is None   # sin intereses


def test_manageable_debt_is_not_measured_in_exempt_sectors():
    bank = TickerInfo("JPM", sector="Financial Services", debt_to_equity=9.0, interest_coverage=0.4)
    assert reject(bank, require_manageable_debt=True) is None


def test_manageable_debt_thresholds_come_from_the_configuration():
    s = Settings(scanner={"quality": {"manageable_debt": {"max_debt_to_equity": 1.0, "min_interest_coverage": 5.0}}})
    c = criteria_from_settings(s)
    assert (c.manageable_max_de, c.manageable_min_cover) == (1.0, 5.0)


# ---- presets de calidad -------------------------------------------------------------------------------------
def test_quality_presets_are_validated():
    with pytest.raises(ValueError, match="filtros desconocidos"):
        QualityPreset(name="x", levels={"q_nada": "standard"})
    with pytest.raises(ValueError, match="grados desconocidos"):
        QualityPreset(name="x", levels={"q_de": "ultra"})
    assert len(QualitySettings().presets) == 2                                    # Caja (4 filtros) y Deuda sana


def test_the_default_presets_are_the_filters_of_the_analyst_and_none_is_active_by_default(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    assert "Caja (4 filtros)" in page and "Deuda sana" in page
    caja = re.search(r"data-levels='([^']*)' data-manage=\"0\"[^>]*>Caja \(4 filtros\)", page)
    assert json.loads(caja.group(1)) == {"q_ocfd": "standard", "q_capex": "standard", "q_bb": "strict", "q_fcfa": "strict"}
    assert 'class="preset qpreset active"' not in page                           # ninguna combinación activa por defecto
    assert re.search(r'name="q_manage" id="q_manage" *>', page)                  # «deuda baja o manejable» desmarcada
    assert 'selected' not in page.split('name="q_de"')[1].split('</select>')[0]  # y los selectores de solvencia sin elegir


def test_a_preset_marks_itself_active_when_its_levels_are_in_the_url(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE + "&q_de=standard&q_cash=standard&q_cov=standard").text
    assert re.search(r'class="preset qpreset active"[^>]*>Deuda sana', page)
    assert not re.search(r'class="preset qpreset active"[^>]*>Caja', page)


# ---- el formulario del Scanner filtra con la solvencia -------------------------------------------------------
def seed_quality(svc):
    """AAPL (en la watchlist) con datos de calidad; el resto de la watchlist de prueba no tiene."""
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, debt_to_equity=2.0, interest_coverage=1.0, cash_to_short_debt=0.2,
                                 ocf_to_debt=0.05, capex_to_ocf=0.8, fcf_to_assets=0.01, net_buyback_pct=-2.0)])


def test_the_scanner_applies_solvency_levels_from_the_url(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    seed_quality(svc)
    assert "AAPL" in client.get(BASE).text
    page = client.get(BASE + "&q_de=strict").text                                 # AAPL tiene deuda/patrimonio 2,0 > 0,5
    assert "AAPL" not in re.findall(r'name="sel" value="([^"]*)"', page) and "Deuda / patrimonio: Estricto" in page
    ok = client.get(BASE + "&q_de=flexible").text
    assert "Deuda / patrimonio: Flexible" in ok                                    # 2,0 > 1,5: tampoco pasa el flexible
    page = client.get(BASE + "&q_manage=on").text                                  # D/E 2,0 y cobertura 1,0: deuda alta y mal cubierta
    assert "Deuda baja o manejable" in page and "AAPL" not in re.findall(r'name="sel" value="([^"]*)"', page)


def test_an_invalid_level_is_reported_in_the_scanner(client_and_service):
    client, svc, gw, _ = client_and_service
    assert "grado de exigencia no permitido" in client.get(BASE + "&q_de=ultra").text


def test_each_option_shows_how_many_tickers_of_the_watchlist_pass_it(client_and_service):
    client, svc, gw, _ = client_and_service
    seed_quality(svc)
    page = client.get("/scanner?reset=1").text
    assert re.search(r"Estricto \(≤ 0\.5\) · 0/1", page)                           # la watchlist de prueba tiene 1 ticker (AAPL)
    assert re.search(r"Beneficios en los últimos 12 meses · 1/1", page)           # AAPL tiene EPS > 0
    assert re.search(r"Deuda baja o manejable · 0/1", page)


def test_the_universe_keeps_the_columns_but_has_no_quality_filters(client_and_service):
    client, svc, gw, _ = client_and_service
    from tests.integration.test_web import load_rank
    load_rank(client)
    page = client.get("/universe").text
    assert "<th>Deuda/Patr.</th>" in page and 'name="q_profit"' not in page


# ---- panel de impacto ---------------------------------------------------------------------------------------
def test_the_impact_panel_lists_only_the_active_filters_most_restrictive_first(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    seed_quality(svc)
    page = client.get(BASE.replace("/scanner", "/scanner/impact") + "&q_profit=on&q_de=strict").text
    assert "Solo este filtro deja" in page and "Solo él descarta" in page
    assert "Deuda/patrimonio" in page and "Beneficios (12 meses)" in page
    assert page.index("Deuda/patrimonio") < page.index("Beneficios (12 meses)")      # el más restrictivo primero (deja 0 frente a todos)
    assert "muy restrictivo" in page and "Ningún contrato pasa todos los filtros" in page


def test_the_impact_panel_without_filters_says_so(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    url = BASE.replace("/scanner", "/scanner/impact").replace("min_yield=1", "min_yield=0")   # sin yield mínimo ni filtros marcados
    assert "No hay ningún filtro activo" in client.get(url).text
    assert "Yield anual mínimo" in client.get(BASE.replace("/scanner", "/scanner/impact")).text   # el yield mínimo es un filtro más


def test_the_scanner_page_has_the_lazy_impact_panel(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE).text
    assert 'id="viz-box"' in page and 'data-tab="tab-impact"' in page and 'data-url="/scanner/impact"' in page   # pestaña de impacto, carga diferida
    assert 'data-tab="tab-overview"' in page and 'class="tab active" role="tab" data-tab="tab-overview"' in page   # la vista general es la pestaña inicial


# ---- ROIC y años con pérdidas ---------------------------------------------------------------------------------
def test_roic_and_loss_years_rules_use_the_configured_levels():
    t = Settings().scanner.quality.thresholds
    assert (t.roic["flexible"], t.roic["standard"], t.roic["strict"]) == (0.08, 0.12, 0.20)
    assert (t.loss_years["flexible"], t.loss_years["standard"], t.loss_years["strict"]) == (2, 1, 0)
    good = TickerInfo("A", roic=0.25, loss_years=0, fiscal_years=10)
    middling = TickerInfo("B", roic=0.10, loss_years=1, fiscal_years=10)
    assert reject(good, min_roic=t.roic["strict"], max_loss_years=0) is None
    assert "ROIC inferior" in reject(middling, min_roic=t.roic["standard"])
    assert reject(middling, min_roic=t.roic["flexible"], max_loss_years=1) is None
    assert "años con pérdidas" in reject(middling, max_loss_years=0)


def test_missing_roic_or_short_history_counts_as_not_meeting_but_exempt_sectors_pass():
    assert reject(TickerInfo("A"), min_roic=0.1) is not None                       # sin dato
    assert reject(TickerInfo("A", loss_years=None, fiscal_years=4), max_loss_years=2) is not None    # menos de 5 años de historia
    bank = TickerInfo("JPM", sector="Financial Services")
    assert reject(bank, min_roic=0.2, max_loss_years=0) is None                    # exenta: no se mide


def test_the_scanner_form_offers_roic_and_loss_years_with_their_thresholds(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    assert 'name="q_roic"' in page and 'name="q_loss"' in page
    assert "Estándar (≥ 12 %)" in page and "Estricto (≥ 20 %)" in page and "Flexible (≤ 2)" in page and "Estricto (≤ 0)" in page


def test_roic_and_loss_years_filter_the_scan_and_show_in_the_chips(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, roic=0.10, loss_years=0, fiscal_years=10)])
    assert "ROIC: Estricto" in client.get(BASE + "&q_roic=strict").text
    shown = lambda q: {v.split("|")[0] for v in re.findall(r'name="sel" value="([^"]*)"', client.get(BASE + q).text)}   # noqa: E731
    assert "AAPL" in shown("&q_roic=flexible") and "AAPL" not in shown("&q_roic=standard")  # 10 % ≥ 8 % pero < 12 %
    assert "AAPL" in shown("&q_loss=strict")                                                # ningún año con pérdidas


def test_the_universe_shows_the_new_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    from tests.integration.test_web import load_rank
    load_rank(client)
    svc.quality.save([TickerInfo("KO", roic=0.16, loss_years=2, fiscal_years=10)])
    page = client.get("/universe").text
    assert "<th>ROIC</th>" in page and "<th>Años con pérdidas</th>" in page
    ko = [row for row in page.split("<tr>") if 'value="KO"' in row][0]
    assert "16%" in ko and "2/10" in ko and 'title="Años fiscales con pérdidas de los últimos 10"' in ko


def test_quality_repo_round_trips_the_new_fields():
    from scanner_opciones.storage.db import Database
    from scanner_opciones.storage.repositories import QualityRepo
    repo = QualityRepo(Database(":memory:"))
    repo.save([TickerInfo("KO", roic=0.16, loss_years=2, fiscal_years=10)])
    got = repo.get("KO")
    assert (got.roic, got.loss_years, got.fiscal_years) == (0.16, 2, 10)


# ---- ingresos crecientes y estabilidad de los beneficios ------------------------------------------------------
def test_revenue_drops_and_volatility_rules_use_the_configured_levels():
    t = Settings().scanner.quality.thresholds
    assert [t.revenue_drops[k] for k in ("flexible", "standard", "strict")] == [2, 1, 0]
    assert [t.earnings_volatility[k] for k in ("flexible", "standard", "strict")] == [0.80, 0.50, 0.30]
    steady = TickerInfo("A", revenue_drop_years=0, revenue_years=9, earnings_volatility=0.20)
    jumpy = TickerInfo("B", revenue_drop_years=3, revenue_years=9, earnings_volatility=0.70)
    assert reject(steady, max_revenue_drops=0, max_earnings_volatility=0.30) is None
    assert "caída de ingresos" in reject(jumpy, max_revenue_drops=2)
    assert "volatilidad de los beneficios" in reject(jumpy, max_earnings_volatility=0.50)
    assert reject(jumpy, max_revenue_drops=3, max_earnings_volatility=0.80) is None
    assert reject(TickerInfo("C"), max_revenue_drops=2) is not None                  # sin dato = no cumple
    assert reject(TickerInfo("JPM", sector="Financial Services"), max_revenue_drops=0, max_earnings_volatility=0.1) is None


def test_the_scanner_form_offers_revenue_drops_and_earnings_volatility(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    assert 'name="q_revdrop"' in page and 'name="q_evol"' in page
    assert "Estándar (≤ 50 %)" in page and "Estricto (≤ 30 %)" in page and "Años con caída de ingresos" in page


def test_revenue_drops_and_volatility_filter_the_scan(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, revenue_drop_years=1, revenue_years=9, earnings_volatility=0.45)])
    shown = lambda q: {v.split("|")[0] for v in re.findall(r'name="sel" value="([^"]*)"', client.get(BASE + q).text)}   # noqa: E731
    assert "AAPL" in shown("&q_revdrop=standard") and "AAPL" not in shown("&q_revdrop=strict")      # 1 caída: ≤ 1 sí, ≤ 0 no
    assert "AAPL" in shown("&q_evol=standard") and "AAPL" not in shown("&q_evol=strict")            # 0,45 ≤ 0,50 pero > 0,30
    assert "Volatilidad de los beneficios: Estricto" in client.get(BASE + "&q_evol=strict").text


def test_the_universe_shows_the_stability_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    from tests.integration.test_web import load_rank
    load_rank(client)
    svc.quality.save([TickerInfo("KO", revenue_drop_years=3, revenue_years=9, earnings_volatility=0.35)])
    page = client.get("/universe").text
    assert "<th>Caídas de ingresos</th>" in page and "<th>Volatilidad beneficios</th>" in page
    ko = [row for row in page.split("<tr>") if 'value="KO"' in row][0]
    assert "3/9" in ko and "35%" in ko and "color:#b3361b" in ko                    # >1 caída se marca en rojo


def test_quality_repo_round_trips_the_stability_fields():
    from scanner_opciones.storage.db import Database
    from scanner_opciones.storage.repositories import QualityRepo
    repo = QualityRepo(Database(":memory:"))
    repo.save([TickerInfo("KO", revenue_drop_years=2, revenue_years=9, earnings_volatility=0.31)])
    got = repo.get("KO")
    assert (got.revenue_drop_years, got.revenue_years, got.earnings_volatility) == (2, 9, 0.31)
