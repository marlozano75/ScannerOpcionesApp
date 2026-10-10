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


def test_the_scanner_has_no_charts_but_keeps_the_impact_panel_and_inline_bars(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE).text
    assert "data-nearest" not in page and "Contratos por sector" not in page and "Yield anual frente al descuento" not in page
    assert 'id="viz-box"' in page and 'data-url="/scanner/impact"' in page                  # el panel de impacto se mantiene
    assert re.search(r'class="hl cb" style="--p:\d+%"', page)                              # barra del yield dentro de la celda
    assert 'id="results-col"' in page                                                       # la columna de resultados se desplaza entera


def test_every_scanner_filter_section_is_in_the_page_and_explanations_are_notes_the_script_hides(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    sections = re.findall(r'<details class="fx[^"]*" id="(sec-[a-z]+)"', page)
    assert set(sections) == {"sec-contract", "sec-yield", "sec-trend", "sec-levels", "sec-price", "sec-ma", "sec-quality", "sec-optional"}
    assert page.count('class="note"') >= 6 and ".note-help { display:none; }" in page          # notas ocultas por el script, visibles sin JS
    assert "open = { 'sec-contract': true, 'sec-yield': true }" in page                       # primera visita: solo las dos primeras abiertas


def test_the_watchlist_has_an_overview_and_the_universe_a_side_menu_of_sources(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    watch = client.get("/watchlist").text
    assert "Dónde está la volatilidad" in watch and "Tickers por sector" in watch
    load_rank(client)
    uni = client.get("/universe?reset=1").text
    assert 'class="src-menu"' in uni and 'class="shell uni"' in uni and "Cargar ficheros" in uni and "Añadir tickers" in uni
    assert "Acciones por sector" not in uni and "Ficheros cargados" not in uni and "Explorar por fuente" not in uni   # fuera lo informativo
    assert re.search(r'class="src-item active" href="/universe\?reset=1"', uni)              # «Todas» marcada
    assert 'class="help-scope"' in uni


def test_the_chart_tooltip_script_uses_textcontent_and_keyboard_focus(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/").text
    assert "viz-tip" in page and "textContent" in page and "focusin" in page and "pointermove" in page
