"""Interfaz visual: gráficos en el Panel, el Scanner, la Watchlist y el Universo, y explicaciones bajo demanda."""
import re

from tests.integration.test_web import BASE, client_and_service, load_hello, load_rank, refresh  # noqa: F401


def test_the_dashboard_draws_meters_the_vix_curve_and_the_weekly_bars(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/").text
    assert page.count('role="meter"') == 3                                          # los tres cushion, con sus umbrales
    assert 'aria-label="Cierres recientes del VIX"' in page and "Curva de futuros" in page
    assert "Contango" in page or "Backwardación" in page or "Sin futuros del VIX" in page
    assert 'class="hb"' in page and "Si te asignan todo" in page and "Ver como tabla" in page   # barras + tabla alternativa


def test_the_scanner_has_an_overview_with_tabs_and_inline_bars(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE).text
    assert "svg" in page and "data-nearest" in page and "Contratos por sector" in page and "Contratos por tramo de yield anual" in page
    assert re.search(r'class="hl cb" style="--p:\d+%"', page)                              # barra del yield dentro de la celda
    assert 'id="results-col"' in page                                                    # la columna de resultados se desplaza entera


def test_the_scanner_overview_is_absent_without_results(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get(BASE + "&discount=29&min_yield=900").text
    assert 'data-tab="tab-overview"' not in page and "Ningún contrato cumple" in page


def test_every_scanner_filter_section_is_in_the_page_and_explanations_are_notes_the_script_hides(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    sections = re.findall(r'<details class="fx[^"]*" id="(sec-[a-z]+)"', page)
    assert set(sections) == {"sec-contract", "sec-yield", "sec-trend", "sec-levels", "sec-price", "sec-ma", "sec-quality", "sec-optional"}
    assert page.count('class="note"') >= 6 and ".note-help { display:none; }" in page          # notas ocultas por el script, visibles sin JS
    assert "if (!open) {" in page and "open[d.id] = true" in page                             # primera visita: todo abierto


def test_the_watchlist_and_universe_have_overviews(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    watch = client.get("/watchlist").text
    assert "Dónde está la volatilidad" in watch and "Tickers por sector" in watch
    load_rank(client)
    uni = client.get("/universe?reset=1").text
    assert "Acciones por sector" in uni and "Cobertura" in uni and "En watchlist" in uni
    assert 'class="help-scope"' in uni


def test_the_chart_tooltip_script_uses_textcontent_and_keyboard_focus(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/").text
    assert "viz-tip" in page and "textContent" in page and "focusin" in page and "pointermove" in page
