"""Calidad de la empresa: bloque de solvencia, «deuda baja o manejable» y combinaciones (barra de filtros del Universo), liquidez
y resultados (Scanner) y el panel «¿Cuánto descarta cada filtro?»."""
import json
import re
from json import dumps as json_dumps

import pytest

from scanner_opciones.config.settings import QualityPreset, QualitySettings
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.quality import ticker_quality_reject
from tests.integration.test_web import BASE, client_and_service, load_rank, refresh  # noqa: F401
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
    load_rank(client)
    page = client.get("/universe?reset=1").text
    assert "Caja (4 filtros)" in page and "Deuda sana" in page
    caja = re.search(r"data-levels='([^']*)' data-manage=\"0\"[^>]*>Caja \(4 filtros\)", page)
    assert json.loads(caja.group(1)) == {"q_ocfd": "standard", "q_capex": "standard", "q_bb": "strict", "q_fcfa": "strict"}
    assert 'class="preset qpreset active"' not in page                           # ninguna combinación activa por defecto
    assert "checked" not in re.search(r'<input[^>]*id="q_manage"[^>]*>', page).group(0)   # «deuda manejable» desmarcada
    assert 'selected' not in page.split('name="q_de"')[1].split('</select>')[0]  # y los selectores de solvencia sin elegir


def test_a_preset_marks_itself_active_when_its_levels_are_in_the_url(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    page = client.get("/universe?q_de=standard&q_cash=standard&q_cov=standard").text
    assert re.search(r'class="preset qpreset active"[^>]*>Deuda sana', page)
    assert not re.search(r'class="preset qpreset active"[^>]*>Caja', page)


# ---- el formulario del Scanner filtra con la solvencia -------------------------------------------------------
def seed_quality(svc):
    """AAPL (en la watchlist) con datos de calidad; el resto de la watchlist de prueba no tiene."""
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, debt_to_equity=2.0, interest_coverage=1.0, cash_to_short_debt=0.2,
                                 ocf_to_debt=0.05, capex_to_ocf=0.8, fcf_to_assets=0.01, net_buyback_pct=-2.0)])


def universe_shown(client, query):
    return set(re.findall(r'name="sel" value="([^"]*)"', client.get("/universe?" + query).text))


def test_the_universe_applies_solvency_levels_from_the_url(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    svc.quality.save([TickerInfo("KO", eps_ttm=5.0, debt_to_equity=2.0, interest_coverage=1.0, cash_to_short_debt=0.2,
                                 ocf_to_debt=0.05, capex_to_ocf=0.8, fcf_to_assets=0.01, net_buyback_pct=-2.0)])
    assert "KO" in universe_shown(client, "")
    assert "KO" not in universe_shown(client, "q_de=strict")                       # deuda/patrimonio 2,0 > 0,5
    assert "KO" not in universe_shown(client, "q_de=flexible")                     # 2,0 > 1,5: tampoco pasa el flexible
    assert "KO" not in universe_shown(client, "q_manage=on")                       # D/E 2,0 y cobertura 1,0: deuda alta y mal cubierta
    assert "1 activo" in client.get("/universe?q_de=strict").text


def test_an_invalid_level_is_reported_in_the_universe(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    assert "grado de exigencia no permitido" in client.get("/universe?q_de=ultra").text


def test_the_scanner_shows_how_many_tickers_of_the_watchlist_pass_each_liquidity_option(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, option_liquidity=3)])
    page = client.get("/scanner?reset=1").text
    assert re.search(r"3 o más · 1/1", page) and re.search(r"4 o más · 0/1", page)   # la watchlist de prueba tiene 1 ticker (AAPL)


def test_the_universe_keeps_the_columns_and_has_the_quality_filters(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    page = client.get("/universe").text
    assert "<th>Deuda/Patr.</th>" in page and 'name="q_profit"' in page
    assert 'name="q_profit"' not in client.get("/scanner").text


# ---- panel de impacto ---------------------------------------------------------------------------------------
def test_the_impact_panel_lists_only_the_active_filters_most_restrictive_first(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    svc.quality.save([TickerInfo("AAPL", eps_ttm=5.0, option_liquidity=2, next_earnings=None)])
    page = client.get(BASE.replace("/scanner", "/scanner/impact") + "&q_liq=4").text
    assert "Solo este filtro deja" in page and "Solo él descarta" in page
    assert "Liquidez de las opciones" in page and "Beneficios (12 meses)" not in page
    assert "muy restrictivo" in page and "Ningún contrato pasa todos los filtros" in page


def test_the_impact_panel_without_filters_says_so(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    url = BASE.replace("/scanner", "/scanner/impact").replace("min_yield=1", "min_yield=0")   # sin yield mínimo ni filtros marcados
    assert "No hay ningún filtro activo" in client.get(url).text
    assert "Yield mínimo del periodo" in client.get(BASE.replace("/scanner", "/scanner/impact")).text   # el yield mínimo es un filtro más


def test_the_scanner_page_has_the_lazy_impact_panel(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE).text
    assert 'id="viz-box"' in page and 'data-url="/scanner/impact"' in page and "¿Cuánto descarta cada filtro?" in page   # carga diferida
    assert 'data-tab=' not in page and "tab-overview" not in page                                                       # sin pestañas ni vista general


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


def test_the_universe_form_offers_roic_and_loss_years_with_their_thresholds(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    page = client.get("/universe").text
    assert 'name="q_roic"' in page and 'name="q_loss"' in page
    assert "Estándar (≥ 12 %)" in page and "Estricto (≥ 20 %)" in page and "Flexible (≤ 2)" in page and "Estricto (≤ 0)" in page


def test_roic_and_loss_years_filter_the_universe(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    svc.quality.save([TickerInfo("KO", eps_ttm=5.0, roic=0.10, loss_years=0, fiscal_years=10)])
    shown = lambda q: universe_shown(client, q)   # noqa: E731
    assert "KO" in shown("q_roic=flexible") and "KO" not in shown("q_roic=standard")      # 10 % ≥ 8 % pero < 12 %
    assert "KO" in shown("q_loss=strict")                                                 # ningún año con pérdidas
    assert "ROIC" in client.get("/universe?q_roic=strict").text


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


# ---- los botones de calidad no deben vaciar el strike y el vencimiento ------------------------------------------
def test_the_strike_presets_are_links_and_quality_presets_stay_in_the_universe(client_and_service):
    """Los perfiles TPC del Scanner son enlaces (sin manejador JS) y los botones de combinaciones de calidad (`preset qpreset`)
    están en el Universo: el Scanner no los lleva."""
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    assert "querySelectorAll('button.preset')" not in page and 'qpreset' not in page
    assert re.search(r'<a class="preset" href="/scanner\?[^"]*discount=20[^"]*"[^>]*>Mensual \(TPC\)</a>', page)
    load_rank(client)
    assert 'class="preset qpreset' in client.get("/universe").text and "data-discount" not in client.get("/universe").text


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


def test_the_universe_form_offers_revenue_drops_and_earnings_volatility(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    page = client.get("/universe").text
    assert 'name="q_revdrop"' in page and 'name="q_evol"' in page
    assert "Estándar (≤ 50 %)" in page and "Estricto (≤ 30 %)" in page and "Años con caída de ingresos" in page


def test_revenue_drops_and_volatility_filter_the_universe(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    svc.quality.save([TickerInfo("KO", eps_ttm=5.0, revenue_drop_years=1, revenue_years=9, earnings_volatility=0.45)])
    shown = lambda q: universe_shown(client, q)   # noqa: E731
    assert "KO" in shown("q_revdrop=standard") and "KO" not in shown("q_revdrop=strict")      # 1 caída: ≤ 1 sí, ≤ 0 no
    assert "KO" in shown("q_evol=standard") and "KO" not in shown("q_evol=strict")            # 0,45 ≤ 0,50 pero > 0,30


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


# ---- estrategia de venta de puts: botón superior y grupos de filtros ----------------------------------------------
def test_the_strategy_button_activates_every_related_filter_and_marks_itself_on(client_and_service):
    from urllib.parse import parse_qs
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    href = re.search(r'<a class="preset strategy" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert (q["dte_min"], q["dte_max"], q["delta_max"], q["ivr"], q["ivp"]) == ("30", "45", "0.3", "50", "50")
    assert "delta_min" not in q and q["signals"] == "2" and not any("exp" in k for k in q)   # solo delta máximo; ≥ 2 señales
    assert q["ma200"] == "above" and "q_earn" not in q and q["q_liq"] == "3"        # evitar resultados: desactivado por defecto
    assert all(q[k] == "on" for k in ("use_delta", "use_near", "use_ivr", "use_ivp"))
    on = client.get(href).text
    assert 'class="preset strategy active"' in on and "Parámetro no válido" not in on                    # la estrategia cargada se marca
    for chip in ("|Δ| ≤ 0.3", "≥ 2 señales de entrada", "IV Rank ≥ 50", "A ≤ 3 % de MA50 o soporte"):
        assert chip in on
    assert 'class="preset strategy active"' in client.get(href.replace("delta_max=0.3", "delta_max=0.4")).text   # el perfil activo es el de la URL (profile=)


def test_invalid_delta_and_near_values_are_reported(client_and_service):
    client, svc, gw, _ = client_and_service
    for bad in ("use_delta=on&delta_min=0.4&delta_max=0.3", "use_delta=on&delta_min=abc&delta_max=0.3",
                "use_near=on&near_pct=0", "use_delta=on&delta_min=&delta_max=", "signals=9"):
        assert "Parámetro no válido" in client.get(BASE + "&" + bad).text, bad
    assert "Parámetro no válido" not in client.get(BASE + "&delta_min=0.4&delta_max=0.3").text   # desmarcado: la caja no se lee
    assert "Parámetro no válido" not in client.get(BASE + "&use_delta=on&delta_min=&delta_max=0.3").text   # solo máximo
    assert "Parámetro no válido" not in client.get(BASE + "&use_delta=on&delta_min=0.2&delta_max=").text   # solo mínimo


def test_the_timing_filters_are_in_the_entry_group_and_the_strategy_only_turns_on_the_rsi(client_and_service):
    from urllib.parse import parse_qs
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    entry = page.split('id="sec-entry"')[1].split("</details>")[0]
    for name, value in (("rsi_max", "35"), ("boll_pctb", "0.3"), ("boll_width", "6")):
        assert f'name="{name}" value="{value}"' in entry
    href = re.search(r'<a class="preset strategy" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert q["use_rsi"] == "on" and q["rsi_max"] == "35" and "use_boll" not in q and "use_bw" not in q
    on = client.get(href).text
    assert 'class="preset strategy active"' in on and "RSI rebota desde ≤ 35" in on
    assert "Bollinger %B ≤ 0.3" in client.get(BASE + "&use_boll=on&boll_pctb=0.3").text


def test_invalid_timing_values_are_reported(client_and_service):
    client, svc, gw, _ = client_and_service
    for bad in ("use_rsi=on&rsi_max=100", "use_rsi=on&rsi_max=x", "use_boll=on&boll_pctb=5", "use_bw=on&boll_width=0"):
        assert "Parámetro no válido" in client.get(BASE + "&" + bad).text, bad


def test_the_strategy_button_also_loads_the_iv_filters_and_the_vix_is_only_a_message_in_the_panel(client_and_service):
    from urllib.parse import parse_qs
    from scanner_opciones.domain.models import VixData
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get("/scanner?reset=1").text
    iv = page.split('id="sec-iv"')[1].split("</details>")[0]
    for name, value in (("ivhv", "1.2"), ("ivchg", "0")):
        assert f'name="{name}" value="{value}"' in iv
    assert "vix_min" not in page and "use_vix" not in page                       # el VIX no es un filtro del scanner
    href = re.search(r'<a class="preset strategy" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert all(q[k] == "on" for k in ("use_ivhv", "use_ivchg")) and not any("vix" in k for k in q)
    on = client.get(href).text
    assert 'class="preset strategy active"' in on and "IV/HV ≥ 1.2" in on
    svc.state.vix = VixData(current=11.0, last_closes=[], futures=[], updated_at=None)
    panel = client.get("/").text
    assert 'class="banner warn"' in panel and "primas pobres" in panel          # el aviso está junto al gráfico del VIX


def test_invalid_iv_values_are_reported(client_and_service):
    client, svc, gw, _ = client_and_service
    for bad in ("use_ivhv=on&ivhv=0", "use_ivhv=on&ivhv=x", "use_ivchg=on&ivchg=x"):
        assert "Parámetro no válido" in client.get(BASE + "&" + bad).text, bad


def test_the_atr_filter_has_its_own_group_and_the_strategy_button_turns_it_on(client_and_service):
    from urllib.parse import parse_qs
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    entry = page.split('id="sec-entry"')[1].split("</details>")[0]
    atr = page.split('id="sec-atr"')[1].split("</details>")[0]
    assert 'name="atr_mult" value="2"' in atr and "atr_mult" not in entry
    href = re.search(r'<a class="preset strategy" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert q["use_atr"] == "on" and q["atr_mult"] == "2"
    on = client.get(href).text
    assert 'class="preset strategy active"' in on and "Strike a ≥ 2 ATR" in on
    for bad in ("use_atr=on&atr_mult=0", "use_atr=on&atr_mult=x", "use_atr=on&atr_mult=11"):
        assert "Parámetro no válido" in client.get(BASE + "&" + bad).text, bad


def test_the_atr_filter_does_not_discard_anything_without_highs_and_lows_and_the_impact_panel_counts_it(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert "Parámetro no válido" not in client.get(BASE + "&use_atr=on&atr_mult=2").text
    shown = lambda q: set(re.findall(r'name="sel" value="([^"]*)"', client.get(BASE + q).text))   # noqa: E731
    assert shown("") and shown("&use_atr=on&atr_mult=2") == shown("")               # sin máximos ni mínimos guardados: sin ATR, no descarta
    impact = client.get(BASE.replace("/scanner", "/scanner/impact") + "&use_atr=on&atr_mult=2").text
    assert "Strike a N ATR del precio" in impact


def test_the_panel_shows_the_put_management_card(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/").text
    assert "Gestión de puts vendidas" in page


def test_the_scanner_form_has_the_signals_selector_and_the_sigma_column(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE + "&signals=2&use_near=on&near_pct=3&use_rsi=on&rsi_max=35").text
    entry = page.split('id="sec-entry"')[1].split("</details>")[0]
    assert 'name="signals"' in entry and '<option value="2" selected>' in entry and "≥ 2 señales de entrada" in page
    assert "Dist. σ" not in page and 'name="use_exp"' in page          # el filtro de movimiento esperado vuelve (2026-10-15); la columna σ no
    on = client.get(BASE + "&use_exp=on&exp_mult=1").text
    assert "Parámetro no válido" not in on and "Fuera de 1× mov. esperado" in on
    assert "Parámetro no válido" in client.get(BASE + "&use_exp=on&exp_mult=9").text or "entre 0 y 5" in client.get(BASE + "&use_exp=on&exp_mult=9").text


def test_an_empty_scan_explains_why_and_what_would_recover_contracts(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE + "&use_oi=on&oi=100000000&use_bidsize=on&bidsize=99999999").text
    assert "Ningún contrato pasa todos los filtros" in page and "OI mínimo" in page and "Bid size mínimo" in page
    assert "Ningún contrato pasa todos los filtros" not in client.get(BASE).text      # con resultados no hay aviso


def test_put_profile_is_assigned_by_opening_dte_and_can_be_corrected(client_and_service):
    from datetime import timedelta
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    client, svc, gw, _ = client_and_service
    option = OptionContract("AAPL", svc.now().date() + timedelta(days=20), 90.0, OptionRight.PUT)
    svc.state.positions = [Position("AAPL", -1, -90.0, "Technology", option, 110.0)]       # 18 % de la prima ganada a 20 DTE
    key = f"AAPL|{option.expiry.isoformat()}|90"
    page = client.get("/").text
    assert f'name="key" value="{key}"' in page
    assert '<option value="Tortuga" selected>' in page and "Revisar: quedan" in page        # 20 DTE no encaja en ningún perfil: Tortuga (gestión Tastytrade)
    r = client.post("/management/profile", data={"key": key, "profile": "Mensual (TPC)"}, follow_redirects=False)
    assert r.status_code == 303 and svc.put_profiles() == {key: "Mensual (TPC)"}
    page = client.get("/").text
    assert "Revisar: quedan" not in page and '<option value="Mensual (TPC)" selected>' in page   # el perfil TPC mantiene hasta el vencimiento
    assert client.post("/management/profile", data={"key": key, "profile": "x"}).status_code == 400
    assert client.post("/management/profile", data={"key": "mal", "profile": "Tortuga"}).status_code == 400
    assert client.post("/management/profile", data={"key": "AAPL|no-fecha|90", "profile": "Tortuga"}).status_code == 400
    assert svc.put_profiles() == {key: "Mensual (TPC)"}
    svc.set_put_profile(f"MSFT|{(svc.now().date() - timedelta(days=1)).isoformat()}|300", "Tortuga")   # ya vencida: se olvida
    assert svc.put_profiles() == {key: "Mensual (TPC)"}


def test_a_put_is_classified_with_the_dte_it_had_the_first_time_the_app_saw_it(client_and_service):
    from datetime import timedelta
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    client, svc, gw, _ = client_and_service
    today = svc.now().date()
    option = OptionContract("AAPL", today + timedelta(days=30), 90.0, OptionRight.PUT)
    svc.state.positions = [Position("AAPL", -1, -90.0, "Technology", option, 110.0)]
    svc._note_put_opened()                                                                      # primera vez: hoy, a 30 DTE
    key = f"AAPL|{option.expiry.isoformat()}|90"
    assert svc.put_assignments() == {key: "Mensual (TPC)"}                                      # 25–35 DTE (rango más estrecho que Tastytrade 30–45)
    svc.meta.set("put_opened", json_dumps({key: (today - timedelta(days=10)).isoformat()}))     # abierta hace 10 días a 40 DTE
    assert svc.put_assignments() == {key: "TastyPut"}
    assert svc.put_management() == {key: "tastytrade"}


def test_the_signals_selector_drops_to_the_number_of_marked_signals(client_and_service):
    client = client_and_service[0]
    one = client.get(BASE + "&signals=2&use_near=on&near_pct=3").text.split('id="sec-entry"')[1].split("</details>")[0]
    assert '<option value="1" selected>' in one
    none = client.get(BASE + "&signals=2").text.split('id="sec-entry"')[1].split("</details>")[0]
    assert '<option value="0" selected>' in none


def test_owned_puts_are_marked_in_the_scanner(client_and_service):
    from scanner_opciones.domain.models import Position
    client, svc, gw, _ = client_and_service
    refresh(client)
    contract = svc.contracts.list()[0]
    page = client.get(BASE).text
    assert "EN CARTERA" not in page.split("<tbody")[-1]
    svc.state.positions = [Position(contract.ticker, -2, -100.0, option=contract)]
    assert svc.owned_options() == {(contract.ticker, contract.expiry, round(contract.strike, 4)): -2}
    page = client.get("/scanner?submitted=1&discount=0&dte_min=0&dte_max=60&min_yield=0").text
    assert "EN CARTERA \u00d72" in page


def test_the_exact_opening_date_from_tortuga_beats_the_first_time_the_app_saw_the_put(client_and_service, tmp_path):
    import json as _json
    from datetime import timedelta
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    client, svc, gw, _ = client_and_service
    f = tmp_path / "aperturas.json"
    object.__setattr__(svc.settings, "community", svc.settings.community.model_copy(update={"openings_path": str(f)}))
    today = svc.now().date()
    option = OptionContract("AAPL", today + timedelta(days=30), 90.0, OptionRight.PUT)
    svc.state.positions = [Position("AAPL", -1, -90.0, "Technology", option, 110.0)]
    svc._note_put_opened()                                                                    # sin fichero: hoy, a 30 DTE
    key = f"AAPL|{option.expiry.isoformat()}|90"
    assert svc.put_open_dates() == {key: (today, False)} and svc.put_assignments() == {key: "Mensual (TPC)"}
    f.write_text(_json.dumps({"version": 1, "generated_at": "2026-10-15T13:00:00+02:00", "source": "tortuga", "puts": [
        {"date": (today - timedelta(days=10)).isoformat(), "ticker": "AAPL", "expiry": option.expiry.isoformat(), "strike": 90, "qty": 1}]}), encoding="utf-8")
    assert svc.put_open_dates() == {key: (today - timedelta(days=10), True)}                  # fecha exacta: abierta a 40 DTE
    assert svc.put_assignments() == {key: "TastyPut"}
    page = client.get("/").text
    assert "fecha exacta de Tortuga" in page and "40 DTE al abrir" in page
    f.write_text("no es json", encoding="utf-8")                                              # un fichero estropeado conserva lo último bueno
    assert svc.put_open_dates()[key][1] is True
