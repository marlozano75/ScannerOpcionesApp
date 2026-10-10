from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.models import AccountSummary, OptionContract, OptionQuote
from scanner_opciones.ui.web import create_app
from tests.integration.test_service import NOW, TODAY, make_service, seed_market


@pytest.fixture
def client_and_service():
    svc, gw = make_service()
    seed_market(gw)
    svc.watchlist.add(["AAPL"], NOW)
    created: list[AccountMode] = []

    def factory(mode):
        created.append(mode)
        return FakeGateway(account=AccountSummary("DU-NEW", net_liquidation=1000, excess_liquidity=100))

    app = create_app(svc, factory, on_startup=svc.start, on_shutdown=svc.stop)
    with TestClient(app) as client:
        client.svc = svc
        client.portal.call(svc.wait_idle)   # la actualización diaria del arranque va en segundo plano
        # tras el arranque, damos cotizaciones a los contratos candidatos
        return_value = (client, svc, gw, created)
        from scanner_opciones.domain.models import OptionQuote
        for c in svc.contracts.list():
            gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
            gw.margins[c] = 1500.0
        yield return_value


def refresh(client):
    assert client.post("/refresh", follow_redirects=False).status_code == 303
    client.portal.call(client.svc.wait_idle)   # el refresco va en segundo plano


def test_dashboard_renders_risk_and_vix(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/")
    assert r.status_code == 200
    assert "Look Ahead" in r.text and "HighestSeverity" in r.text and "Post-Expiration" in r.text
    assert "18.50" in r.text  # VIX
    assert "Leverage Assignment" in r.text and "Gross Position Value" in r.text and "Nominal Assignment Exposure" in r.text






BASE = "/scanner?submitted=1&discount=20&dte_min=25&dte_max=35&min_yield=1"


def _optional_block(html: str) -> str:
    return html.split("Filtros opcionales")[1].split("</form>")[0]


def test_scanner_defaults_from_config(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner")
    assert r.status_code == 200 and "contratos cumplen" in r.text and "AAPL" in r.text
    for name, value in (("discount", "10"), ("dte_min", "1"), ("dte_max", "35"), ("min_yield", "1")):
        assert f'name="{name}"' in r.text and f'name="{name}" id="{name}" size="{6 if "d" in name[:1] and "dte" not in name else 5}" value="{value}"' in r.text.replace(
            'size="6" value', 'size="6" value').replace('size="5" value', 'size="5" value') or f'value="{value}"' in r.text
    assert 'name="discount_max"' not in r.text and "30.0%" in r.text   # descuento máx. fijo = máx. del rango guardado
    assert "checked" not in _optional_block(r.text)          # ningún filtro opcional marcado


def test_scanner_has_no_operation_selector(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner")
    assert 'name="op"' not in r.text and "<option value=\"tactical\"" not in r.text
    assert 'id="dte_min_label"' not in r.text and 'name="dte_min"' in r.text      # DTE mín. siempre editable
    assert "Operación" not in r.text
    assert client.get("/scanner?op=tactical").status_code == 200                   # un enlace antiguo no rompe


def test_optional_filters_only_apply_when_ticked(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    unticked = client.get(BASE + "&oi=100000")               # valor sin casilla: no se aplica
    assert "contratos cumplen" in unticked.text and "Ningún contrato cumple" not in unticked.text
    ticked = client.get(BASE + "&use_oi=on&oi=100000")
    assert "Ningún contrato cumple" in ticked.text and 'name="use_oi" checked' in ticked.text
    blank = client.get(BASE + "&use_oi=on&oi=")
    assert "Parámetro no válido" in blank.text
    assert "Parámetro no válido" in client.get(BASE + "&use_oi=on&oi=abc").text


def test_discount_dte_and_yield_are_editable(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    # DTE 25-35: el contrato guardado (DTE 30) aparece; con DTE 40-50 no
    assert "contratos cumplen" in client.get(BASE).text
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&discount=20&dte_min=40&dte_max=50&min_yield=1").text
    # descuento mínimo 30 %: el strike a 25 % ya no cumple (solo el de 30 %, si existe)
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&discount=30&dte_min=25&dte_max=35&min_yield=1").text
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&discount=20&dte_min=25&dte_max=35&min_yield=50").text
    wide = client.get("/scanner?submitted=1&discount=15&dte_min=1&dte_max=60&min_yield=0.1")
    assert "contratos cumplen" in wide.text and "yield anual ≥ 0.1%" in wide.text and "DTE 1–60" in wide.text


def test_warning_when_outside_stored_range(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner?submitted=1&discount=2&dte_min=25&dte_max=90&min_yield=1")
    assert "Descuento mín. por debajo del rango guardado" in r.text and "Descuento máx." not in r.text
    assert "DTE fuera del rango guardado" in r.text
    ok = client.get(BASE)
    assert "Solo se escanean los contratos guardados" in ok.text and "banner info" not in ok.text.split("</form>")[1].split("<script>")[0]


@pytest.mark.parametrize("qs", [
    "discount=100&dte_min=25&dte_max=35&min_yield=1",   # descuento fuera de rango
    "discount=20&dte_min=40&dte_max=35&min_yield=1",    # DTE mín > máx
    "discount=20&dte_min=25&dte_max=35&min_yield=-1",   # yield negativo
    "discount=&dte_min=25&dte_max=35&min_yield=1",      # vacío
    "discount=x&dte_min=25&dte_max=35&min_yield=1",     # no numérico
    "discount=20&dte_min=2.5&dte_max=35&min_yield=1",   # DTE no entero
    "discount=20&dte_min=25&dte_max=&min_yield=1",      # DTE máx vacío
    "discount=45&dte_min=25&dte_max=35&min_yield=1",                    # descuento mín > máx del rango guardado (40)
])
def test_invalid_form_values(client_and_service, qs):
    client, *_ = client_and_service
    r = client.get("/scanner?submitted=1&" + qs)
    assert r.status_code == 200 and "Parámetro no válido" in r.text


def test_config_filter_values_start_ticked(client_and_service):
    client, svc, gw, _ = client_and_service
    from scanner_opciones.config.settings import Settings
    svc.settings = Settings.model_validate({"scanner": {"filters": {"min_oi": 50}}})
    r = client.get("/scanner")
    assert 'name="use_oi" checked' in r.text and 'value="50"' in r.text
    assert 'name="use_oi" checked' not in client.get(BASE + "&oi=50").text   # enviado sin marcar = desactivado


def test_simulate_flow(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    c = svc.contracts.list()[0]
    key = f"AAPL|{c.expiry}|{c.strike}"
    r = client.post("/simulate", data={"sel": key, f"qty_{key}": "2"})
    assert r.status_code == 200 and "Cushion simulado" in r.text and "Technology" in r.text
    empty = client.post("/simulate", data={})
    assert "Selecciona al menos un contrato" in empty.text


def test_switch_mode_swaps_gateway(client_and_service):
    client, svc, gw, created = client_and_service
    r = client.post("/connection", data={"mode": "live"}, follow_redirects=False)
    assert r.status_code == 303 and created == [AccountMode.LIVE]
    assert svc.state.account.account_id == "DU-NEW"
    assert svc.settings.ibkr.mode is AccountMode.LIVE
    bad = client.post("/connection", data={"mode": "demo"})
    assert bad.status_code == 400


def test_removing_a_ticker_deletes_its_contracts(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert svc.contracts.list("AAPL")
    svc.remove_ticker("AAPL")
    assert svc.contracts.list("AAPL") == [] and svc.snapshots.all("AAPL") == []


def test_scanner_shows_desc_and_bid_size_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner")
    assert "<th>Desc.</th>" in r.text and "<th>Dist.</th>" not in r.text
    assert "<th>Bid size</th>" in r.text


def test_watchlist_and_scanner_tables_are_sortable(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    wl = client.get("/watchlist").text
    assert 'class="sortable" id="watchlist-table"' in wl
    assert "SortableTables.init" in wl and "function sortRows" in wl          # script incluido en la página
    sc = client.get("/scanner").text
    assert 'class="sortable sticky-tk with-sel" id="scan-results"' in sc
    assert "<th data-nosort>Cant.</th>" in sc                                # la columna de cantidad no ordena
    assert "th[aria-sort=descending]::after" in sc and "\\25BC" in sc     # indicador de dirección
    # las tablas del panel no se ordenan
    assert 'class="sortable"' not in client.get("/").text
    # el JS no rompe el renderizado de Jinja: no quedan marcas sin resolver
    assert "{%" not in wl and "{{" not in wl


def test_price_reference_selector_defaults_and_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner")
    assert 'name="ref"' in r.text and 'name="ref_x"' in r.text
    assert 'value="bid_plus_spread" selected' in r.text and 'name="ref_x" id="ref_x" size="5" value="25"' in r.text
    assert "precio de venta: bid + 25% del spread" in r.text
    # columnas agrupadas: ticker, sector y precio juntos; yields juntos (el anual destacado y orden por defecto); delta con la IV
    assert '<th class="tk">Ticker</th><th class="txt">Sector</th><th>Precio</th><th>Strike</th>' in r.text
    assert '<th class="hl" data-sort-default="desc">Yield anual</th><th>Yield bid anual</th>' in r.text
    assert "<th>Delta</th><th>IV</th><th>IV Rank</th><th>IV Pctl</th>" in r.text
    for gone in ("Vence", "Prima ref.", "Yield", "Bid", "Ask", "Last", "Margen ini.", "Yield bid", "Hora precio",
                 "Ex-div (d)", "FCF (M$)", "Actualizado"):
        assert f"<th>{gone}</th>" not in r.text
    assert 'id="ref_x_label" hidden' not in r.text                    # X visible con «Bid + X % del spread»
    mid = client.get(BASE + "&ref=mid&ref_x=25")
    assert "precio de venta: mid (media bid/ask)" in mid.text and 'id="ref_x_label" hidden' in mid.text
    bid = client.get(BASE + "&ref=bid&ref_x=25")
    assert "precio de venta: bid" in bid.text


def test_price_reference_changes_which_contracts_pass(client_and_service):
    client, svc, gw, _ = client_and_service
    from scanner_opciones.domain.models import OptionQuote
    refresh(client)
    for c in svc.contracts.list("AAPL"):          # spread muy ancho: bid 0.40 / ask 1.60 (mid 1.00)
        gw.quotes[c] = OptionQuote(bid=0.40, ask=1.60, open_interest=500)
    refresh(client)
    url = "/scanner?submitted=1&discount=20&dte_min=25&dte_max=35&min_yield=15"
    assert "Ningún contrato cumple" in client.get(url + "&ref=bid&ref_x=25").text          # 0.40/75 = 0.53 %
    assert "Ningún contrato cumple" in client.get(url + "&ref=bid_plus_spread&ref_x=25").text   # 0.70/75 = 0.93 %
    assert "contratos cumplen" in client.get(url + "&ref=mid&ref_x=25").text              # 1.00/75 = 1.33 %
    assert "contratos cumplen" in client.get(url + "&ref=bid_plus_spread&ref_x=75").text  # 1.30/75 = 1.73 % con X=75 %
    # la columna «Yield bid anual» se muestra siempre, aunque el filtro use otra referencia
    assert "<th>Yield bid anual</th>" in client.get(url + "&ref=mid&ref_x=25").text


@pytest.mark.parametrize("qs", [
    "ref=raro&ref_x=25", "ref=bid_plus_spread&ref_x=", "ref=bid_plus_spread&ref_x=abc",
    "ref=bid_plus_spread&ref_x=120", "ref=bid_plus_spread&ref_x=-5",
])
def test_invalid_price_reference(client_and_service, qs):
    client, *_ = client_and_service
    r = client.get(BASE + "&" + qs)
    assert r.status_code == 200 and "Parámetro no válido" in r.text




def test_contracts_tab_is_gone(client_and_service):
    client, *_ = client_and_service
    assert client.get("/contracts").status_code == 404
    assert "/contracts" not in client.get("/scanner").text and "Contratos</a>" not in client.get("/").text


def test_banner_when_market_is_closed_and_not_when_open(client_and_service):
    client, svc, gw, _ = client_and_service

    class Fixed:
        def __init__(self, o):
            self.o = o

        def is_open(self, now):
            return self.o

        def next_open(self, now):
            return datetime(2026, 10, 2, 9, 30)

    svc.market = Fixed(False)
    assert "Mercado cerrado" in client.get("/").text and "02/10 09:30" in client.get("/").text
    svc.market = Fixed(True)
    assert "Mercado cerrado" not in client.get("/").text


def test_ticker_column_is_sticky_in_scanner(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    scanner = client.get("/scanner").text
    assert 'class="sortable sticky-tk with-sel" id="scan-results"' in scanner
    assert '<th class="tk">Ticker</th>' in scanner and '<td class="tk" data-sort="AAPL"><a href="#" class="chart-link" data-ticker="AAPL"' in scanner
    assert '<th class="sel"></th>' in scanner and '<td class="sel"><input type="checkbox"' in scanner   # casilla también fija
    assert "table.sticky-tk .tk { left:0;" in scanner and "position:sticky" in scanner












# ---- pestaña Universo (ficheros xlsx de RankedStocks y HelloStocks elegidos por el usuario) --------

RANK_HEADER = ["Símbolo", "Empresa", "Bolsa", "País", "Capitalización", "Precio", "RS ↓", "Al"]
RANK_ROWS = [
    ["🇺🇸DK", "Delek US Holdings", "NYSE", "US", "$4.4B", "$71.37", "95.6", "Oct 1, 2026"],
    ["🇺🇸KO", "Coca-Cola", "NYSE", "US", "$270.0B", "$60.10", "90.1", "Oct 1, 2026"],
    ["🇺🇸GCT", "GigaCloud", "NASDAQ", "US", "$2.0B", "$52.62", "88.0", "Oct 1, 2026"],
    ["🇺🇸PAYS", "Paysign", "NASDAQ", "US", "$517.1M", "$9.25", "85.6", "Oct 1, 2026"],
]
# columnas del universo con RankedStocks: 0 Símbolo · 1 Fuente · 2 Empresa · 3 Bolsa · 4 País · 5 Capitalización · 6 Precio · 7 RS · 8 Al

LOWER = "Lower Risk (Hello Stocks)"
DEFENSIVE = "Defensive Investing-Benjamin G"
VALUE = "Value Investing-Warren Buffett"


def rank_xlsx(rows=None, header=None) -> bytes:
    import io
    wb = Workbook()
    ws = wb.active
    ws.append(header or RANK_HEADER)
    for r in RANK_ROWS if rows is None else rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def hello_xlsx() -> bytes:
    """HelloStocks: una pestaña por estrategia, cada una con sus columnas; la de Buffett viene sin cabecera."""
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = LOWER
    ws.append(["Ticker", "Company", "Sector", "Criteria", "ROE", "Free Cash Flow (TTM)"])
    ws.append(["KO", "Coca-Cola Co", "Consumer Defensive", datetime(2026, 7, 7), "40.1%", "$10.59 Billion"])
    ws.append(["ADBE", "Adobe Inc", "Technology", datetime(2026, 7, 7), "62.9%", "$716.77 Million"])
    ws2 = wb.create_sheet(DEFENSIVE)
    ws2.append(["Ticker", "Company", "Sector", "Criteria", "PE Ratio", "Dividend Yield"])
    ws2.append(["KO", "Coca-Cola Co", "Consumer Defensive", datetime(2026, 4, 3), "22.5", "2.6%"])
    ws2.append(["AIG", "American International Group Inc", "Financial Services", datetime(2026, 4, 3), "13.31", "2.6%"])
    ws3 = wb.create_sheet(VALUE)
    ws3.append(["ACN", "Accenture plc - Class A", "Technology", datetime(2026, 7, 7), "57.2%", "24.9%", "0.26", "$12.58 Billion", "13.90", "3.51", "3.7%"])
    ws3.append(["ALL", "Allstate Corp (The)", "Financial Services", datetime(2026, 7, 7), "61.5%", "42.7%", "0.22", "$12.25 Billion", "4.55", "1.74", "1.9%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


RANK_NAME = "RankedStocks_2026.10.01.xlsx"
HELLO_NAME = "HelloStocks_2026.10.04.xlsx"


def load_rank(client, **kw):
    return client.post("/universe/load", files=[("files", (RANK_NAME, rank_xlsx(**kw)))], follow_redirects=True)


def load_hello(client):
    return client.post("/universe/load", files=[("files", (HELLO_NAME, hello_xlsx()))], follow_redirects=True)


def test_universe_page_asks_for_files_until_one_is_loaded(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/universe")
    assert r.status_code == 200 and 'name="files"' in r.text and "universe-table" not in r.text
    assert 'href="/universe"' in client.get("/").text                          # pestaña en el menú
    assert 'href="/contracts"' not in client.get("/").text and 'href="/rankedstocks"' not in client.get("/").text
    assert client.get("/contracts").status_code == 404 and client.get("/rankedstocks").status_code == 404


def test_load_rankedstocks_shows_every_column_and_row_with_its_source(client_and_service):
    client, svc, gw, _ = client_and_service
    assert "RankedStocks_2026.10.01.xlsx (1 fuente, 4 filas)" in load_rank(client).text
    r = client.get("/universe?src=RankedStocks")
    for col in ("Ticker", "Fuente", "Empresa", "Sector", "Bolsa", "País", "Capitalización", "Precio", "RS", "Al", "En watchlist"):
        assert f"<th>{col}</th>" in r.text
    assert "4 acciones" in r.text and "Delek US Holdings" in r.text and "$4.4B" in r.text
    assert "<td>RankedStocks</td>" in r.text                                       # la fuente va en cada fila
    assert 'name="sel" value="DK" checked' in r.text and "🇺🇸" not in r.text     # ticker limpio, sin bandera
    assert 'data-sort="4400000000.0"' in r.text                                   # ordena por valor, muestra el texto
    assert [s.name for s in svc.universe_sources] == ["RankedStocks"]


def test_hello_stocks_makes_one_source_per_tab_each_with_its_own_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    r = load_hello(client)
    assert f"{HELLO_NAME} (3 fuentes, 6 filas)" in r.text
    assert [s.name for s in svc.universe_sources] == [LOWER, DEFENSIVE, VALUE]
    lower = client.get(f"/universe?src={LOWER}").text
    assert "<th>ROE</th>" in lower and "<th>Free Cash Flow (TTM)</th>" in lower and "<th>PE Ratio</th>" not in lower
    assert f"<td>{LOWER}</td>" in lower and 'data-sort="10590000000.0"' in lower      # «$10.59 Billion»
    assert "2026-07-07" in lower                                                      # la fecha de «Criteria»
    defensive = client.get(f"/universe?src={DEFENSIVE}").text
    assert "<th>PE Ratio</th>" in defensive and "<th>Dividend Yield</th>" in defensive and "<th>ROE</th>" not in defensive
    value = client.get(f"/universe?src={VALUE}").text                                  # sin cabecera: columnas deducidas
    for col in ("Revenue Growth (5Y)", "ROE", "Debt to Equity", "PB Ratio", "Dividend Yield"):
        assert f"<th>{col}</th>" in value


def test_all_view_has_one_row_per_ticker_with_every_source(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)
    r = client.get("/universe")
    assert "<th>Fuentes</th>" in r.text and "<th>Nº fuentes</th>" in r.text
    ko = [row for row in r.text.split("<tr>") if 'value="KO"' in row]
    assert len(ko) == 1                                                                # KO está en 3 fuentes, una sola fila
    assert "RankedStocks" in ko[0] and LOWER in ko[0] and DEFENSIVE in ko[0] and 'data-sort="3.0"' in ko[0]
    assert "Coca-Cola" in ko[0] and "Consumer Defensive" in ko[0]
    assert "8 acciones" in r.text                                                 # DK KO GCT PAYS + ADBE AIG ACN ALL
    assert 'id="rk-filters"' not in r.text and 'id="quality-form"' in r.text           # solo el panel de calidad




def test_manual_source_needs_a_name_persists_and_reports_the_ones_already_in_it(tmp_path):
    svc, app = _start(tmp_path)
    with TestClient(app) as client:
        load_rank(client)                                                               # KO ya está en RankedStocks
        r = client.post("/universe/manual", data={"text": "ko, nvda amd 123", "source": "Mis ideas"}, follow_redirects=True)
        assert "Añadidos a Mis ideas: KO, NVDA, AMD (2 nuevos en el Universo, el resto ya estaban en otras fuentes)" in r.text
        assert "Rechazados: 123" in r.text                                             # KO se añade: es otra fuente
        assert [s.name for s in svc.universe_sources][-1] == "Mis ideas"
        again = client.post("/universe/manual", data={"text": "NVDA", "source": "mis  IDEAS"}, follow_redirects=True)
        assert "Ya incluidos: NVDA (Mis ideas)" in again.text and svc.manual_sources == {"Mis ideas": ["KO", "NVDA", "AMD"]}
        assert "Mis ideas" in client.get("/universe").text
        nameless = client.post("/universe/manual", data={"text": "TSLA", "source": "  "}, follow_redirects=True)
        assert "Pon un nombre a la fuente" in nameless.text and "TSLA" not in svc.manual_sources["Mis ideas"]
        clash = client.post("/universe/manual", data={"text": "TSLA", "source": "rankedstocks"}, follow_redirects=True)
        assert "ya es una fuente de un fichero" in clash.text
    assert _start(tmp_path)[0].manual_sources == {"Mis ideas": ["KO", "NVDA", "AMD"]}   # sobrevive al reinicio


def test_the_old_single_manual_list_is_migrated_to_a_source_called_manual(tmp_path):
    svc, _ = _start(tmp_path)
    svc.meta.set("universe_manual", '["NVDA", "AMD"]')
    assert svc._load_manual() == {"Manual": ["NVDA", "AMD"]}


def test_tickers_can_be_removed_from_a_manual_source_and_the_source_disappears_when_empty(tmp_path):
    svc, app = _start(tmp_path)
    with TestClient(app) as client:
        client.post("/universe/manual", data={"text": "NVDA AMD KO", "source": "Mis ideas"})
        r = client.post("/universe/source/remove", data={"src": "Mis ideas", "scope": "selected", "sel": ["NVDA", "KO"]},
                        follow_redirects=True)
        assert "Quitados 2 tickers de Mis ideas" in r.text and svc.manual_sources == {"Mis ideas": ["AMD"]}
        gone = client.post("/universe/source/remove", data={"src": "Mis ideas", "scope": "all"}, follow_redirects=True)
        assert "Quitados 1 ticker de Mis ideas" in gone.text and svc.manual_sources == {}
        unknown = client.post("/universe/source/remove", data={"src": "Nada", "scope": "all"}, follow_redirects=True)
        assert "no existe" in unknown.text


def test_tickers_removed_from_a_file_source_stay_out_after_a_restart_until_a_new_file_replaces_it(tmp_path):
    svc, app = _start(tmp_path)
    with TestClient(app) as client:
        load_hello(client)
        before = {r.ticker for s in svc.universe_sources if s.name == LOWER for r in s.table.rows}
        assert "KO" in before
        client.post("/universe/source/remove", data={"src": LOWER, "scope": "selected", "sel": ["KO"]})
        assert "KO" not in {r.ticker for s in svc.universe_sources if s.name == LOWER for r in s.table.rows}
        assert "KO" in {r.ticker for s in svc.universe_sources if s.name == DEFENSIVE for r in s.table.rows}   # solo de esa fuente
    svc2, app2 = _start(tmp_path)                                                       # reinicio
    assert "KO" not in {r.ticker for s in svc2.universe_sources if s.name == LOWER for r in s.table.rows}
    with TestClient(app2) as client:
        client.post("/universe/source/remove", data={"src": LOWER, "scope": "all"})
        assert LOWER not in [s.name for s in svc2.universe_sources]
        load_hello(client)                                                              # un fichero nuevo la restaura entera
        assert {r.ticker for s in svc2.universe_sources if s.name == LOWER for r in s.table.rows} == before


def test_a_selected_source_explains_its_criteria_and_there_is_no_file_removal(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)
    page = client.get(f"/universe?src={LOWER}").text
    assert "Finanzas sólidas, buen crecimiento y precio bajo" in page and "Quitar toda la fuente" in page
    assert "RS Score" in client.get("/universe?src=RankedStocks").text
    assert "guarda la página de HelloStocks con «Strategy Criteria»" in page          # el .xlsx no trae los umbrales
    todas = client.get("/universe?reset=1").text                                     # «Todas»: sin botones de quitar
    assert 'action="/universe/remove"' not in todas and "Quitar toda la fuente" not in todas


def test_apply_add_keeps_existing_and_adds_the_selection(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    gw.prices["GCT"] = 52.0
    r = client.post("/universe/apply", data={"mode": "add", "sel": ["GCT", "PAYS"]}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"AAPL", "GCT", "PAYS"}
    assert "2 nuevos" in r.text
    assert "✓" in client.get("/universe?src=RankedStocks").text                      # la columna «En watchlist» lo marca


def test_apply_replace_swaps_the_whole_watchlist(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    r = client.post("/universe/apply", data={"mode": "replace", "sel": ["DK", "KO"]}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"DK", "KO"}
    assert "Watchlist sustituida: 2 nuevos, 0 conservados, 1 quitados (AAPL)" in r.text


def test_apply_with_nothing_selected_does_not_wipe_the_watchlist(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    r = client.post("/universe/apply", data={"mode": "replace"}, follow_redirects=True)
    assert svc.watchlist.list() == ["AAPL"] and "no se ha cambiado la watchlist" in r.text


def test_page_has_replace_confirmation_and_select_all(client_and_service):
    client, *_ = client_and_service
    page = load_rank(client).text
    assert 'name="mode" value="replace"' in page and "confirmReplace()" in page and 'id="rk-all"' in page


def test_a_newer_file_of_the_same_source_replaces_the_old_one_and_files_can_be_removed(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)
    newer = client.post("/universe/load", files=[("files", ("RankedStocks_2026.10.04.xlsx", rank_xlsx(rows=RANK_ROWS[:2])))],
                        follow_redirects=True)
    assert list(svc.universe_files) == [HELLO_NAME, "RankedStocks_2026.10.04.xlsx"]    # el de 10.01 ya no está
    assert "2 acciones" in client.get("/universe?src=RankedStocks").text and newer.status_code == 200
    for name in [s.name for s in svc.universe_sources if s.name != "RankedStocks"]:
        client.post("/universe/source/remove", data={"src": name, "scope": "all"})
    assert [s.name for s in svc.universe_sources] == ["RankedStocks"]
    assert "<th>Fuentes</th>" in client.get("/universe?src=desconocida").text          # fuente desconocida: vista «Todas»


def test_several_files_in_one_upload_and_bad_files_report_an_error(client_and_service):
    client, svc, gw, _ = client_and_service
    both = client.post("/universe/load", files=[("files", (RANK_NAME, rank_xlsx())), ("files", (HELLO_NAME, hello_xlsx()))],
                       follow_redirects=True)
    assert "Cargado:" in both.text and len(svc.universe_sources) == 4
    bad = client.post("/universe/load", files=[("files", ("w.csv", b"AAPL"))], follow_redirects=True)
    assert "Error" in bad.text and "Formato no soportado" in bad.text
    no_symbol = load_rank(client, header=["Empresa", "Precio"], rows=[["X", "$1"]])
    assert "Error" in no_symbol.text and "cabecera" in no_symbol.text
    assert len(svc.universe_sources) == 4                                             # siguen los ficheros anteriores


def test_activity_banner_shows_the_ibkr_pacing_wait(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.state.activity = "Actualización diaria: 46/73 tickers (último: SLDE)"
    gw.pacing_wait = 0
    assert "esperando el límite" not in client.get("/").text
    gw.pacing_wait = 180                                     # 3 min de espera
    class Busy:
        locked = staticmethod(lambda: True)
    real_lock, svc._lock = svc._lock, Busy()
    try:
        page = client.get("/").text
    finally:
        svc._lock = real_lock
    assert "esperando el límite de peticiones históricas de IBKR" in page and "≈ 3.0 min" in page
    svc.state.activity = None


def test_scanner_remembers_last_filters_when_returning_from_the_menu(client_and_service):
    client = client_and_service[0]
    first = client.get("/scanner")
    assert 'name="discount" id="discount" size="6" value="10"' in first.text      # valores iniciales
    client.get(BASE + "&use_oi=on&oi=77")
    back = client.get("/scanner")                                                  # clic en la pestaña
    assert 'name="discount" id="discount" size="6" value="20"' in back.text and 'value="77"' in back.text
    assert 'name="use_oi" checked' in back.text
    reset = client.get("/scanner?reset=1")
    assert 'name="discount" id="discount" size="6" value="10"' in reset.text
    assert 'value="10"' in client.get("/scanner").text                             # y se olvida lo anterior


def test_simulation_page_does_not_autoreload_and_get_redirects(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    c = svc.contracts.list()[0]
    key = f"AAPL|{c.expiry}|{c.strike}"
    svc.state.activity = "Refrescando"                      # con un refresco en curso otras páginas se recargan solas...
    assert 'http-equiv="refresh"' in client.get("/").text
    assert 'http-equiv="refresh"' not in client.get("/scanner").text     # el scanner tampoco: perdería los contratos marcados
    r = client.post("/simulate", data={"sel": key, f"qty_{key}": "1"})
    assert r.status_code == 200 and 'http-equiv="refresh"' not in r.text    # ...la simulación no (perdería el resultado)
    g = client.get("/simulate", follow_redirects=False)                     # y un GET ya no da «Method Not Allowed»
    assert g.status_code == 303 and g.headers["location"] == "/scanner"


def test_data_version_sube_al_refrescar_y_las_tablas_lo_vigilan(client_and_service):
    client, svc, gw, _ = client_and_service
    before = client.get("/data-version").json()["version"]
    assert f"var current = {before}" in client.get("/scanner?reset=1").text
    assert "/data-version" in client.get("/scanner").text
    assert "/data-version" not in client.get("/").text   # el panel no vigila los datos del scanner
    refresh(client)
    assert client.get("/data-version").json()["version"] > before


def test_scanner_presets_y_botones_de_paso(client_and_service):
    client, svc, gw, _ = client_and_service
    html = client.get("/scanner?reset=1").text
    form = html.split('id="scan-form"')[1].split("</form>")[0]
    # dos configuraciones; la segunda toma el DTE máx. de la ventana guardada (35)
    assert form.count('type="button" class="preset') == 2
    assert 'data-discount="10" data-dte-min="1"' in form and 'data-dte-max="15" data-yield="20"' in form
    assert 'data-discount="20" data-dte-min="16"' in form and 'data-dte-max="35" data-yield="13"' in form
    # − / + en las cajas principales, no en los filtros opcionales
    for name in ("discount", "dte_min", "dte_max", "min_yield"):
        box = form.split(f'name="{name}" id="{name}"')[1].split("</span>")[0]
        assert 'data-d="-1"' in box and 'data-d="1"' in box
    opt = _optional_block(html)
    assert 'data-d=' not in opt and "checked" not in opt
    # las cajas opcionales traen su valor por defecto aunque estén desmarcadas
    for name, value in (("oi", "100"), ("bidsize", "20"), ("spread", "35"), ("ivr", "30"), ("ivp", "50")):
        assert f'<input name="{name}" value="{value}"' in opt
    # los valores de una configuración escanean sin error
    r = client.get("/scanner?submitted=1&discount=20&dte_min=16&dte_max=35&min_yield=13")
    assert r.status_code == 200 and "Parámetro no válido" not in r.text


def test_filtro_opcional_marcado_se_aplica_con_el_valor_de_la_caja(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    base = BASE.replace("min_yield=1", "min_yield=0.1")
    sin = client.get(base + "&oi=100000").text                  # caja con valor pero desmarcada: no filtra
    con = client.get(base + "&oi=100000&use_oi=on").text        # marcada: OI mínimo 100000 deja fuera todo
    assert "AAPL" in sin and '<td class="tk">AAPL' not in con


def _start(tmp_path):
    from scanner_opciones.app.service import AppService
    from scanner_opciones.config.settings import Settings
    from scanner_opciones.storage.db import Database
    from tests.integration.test_service import FixedMarket

    db = Database(tmp_path / "app.db")
    svc = AppService(FakeGateway(), db, Settings(storage={"path": str(tmp_path / "app.db")}),
                     lambda: NOW, market=FixedMarket(True))
    return svc, create_app(svc, lambda mode: FakeGateway())


def test_universe_files_survive_a_restart(tmp_path):
    svc, app = _start(tmp_path)
    with TestClient(app) as client:
        load_rank(client)
        load_hello(client)
    assert (tmp_path / "universe" / RANK_NAME).is_file() and (tmp_path / "universe" / HELLO_NAME).is_file()

    svc2, app2 = _start(tmp_path)                                             # «reinicio»: servicio y app nuevos
    assert [s.name for s in svc2.universe_sources] == ["RankedStocks", LOWER, DEFENSIVE, VALUE]
    assert svc2.universe_files[RANK_NAME][0] == NOW
    with TestClient(app2) as client:
        assert "universe-table" in client.get("/universe").text
        client.post("/universe/source/remove", data={"src": "RankedStocks", "scope": "all"})
    assert [s.name for s in _start(tmp_path)[0].universe_sources] == [LOWER, DEFENSIVE, VALUE]


def test_a_corrupt_saved_file_is_ignored_on_start(tmp_path):
    from scanner_opciones.storage.db import Database

    db = Database(tmp_path / "app.db")
    db.conn.execute("INSERT INTO meta (key, value) VALUES ('universe_files', '{\"x.xlsx\": \"2026-10-01T10:00:00\"}')")
    db.conn.commit()
    (tmp_path / "universe").mkdir()
    (tmp_path / "universe" / "x.xlsx").write_bytes(b"no es un excel")
    svc, _ = _start(tmp_path)
    assert svc.universe_sources == []


def test_no_trend_column_and_no_old_uptrend_filter(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner")
    assert "<th>Tendencia</th>" not in r.text and "<th>Tendencia</th>" not in client.get("/scanner?submitted=1").text
    assert "use_trend" not in r.text and "Solo tendencia alcista" not in r.text        # el filtro antiguo ya no existe
    assert client.get("/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid&use_trend=on").status_code == 200


def test_technical_filters_form(client_and_service):
    client, svc, gw, _ = client_and_service
    base = "/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid"
    r = client.get("/scanner")
    for name in ("trend_dir", "trend_method", "trend_days", "touch", "support", "ma50", "ma100", "ma200", "ema9", "ema20"):
        assert f'name="{name}"' in r.text
    assert "1 mes" in r.text and "1 año" in r.text and "1 semana" in r.text          # opciones con etiquetas legibles
    url = base + "&trend_dir=up&trend_method=swings&trend_days=90&support=on&touch=30&ma50=above&ema9=below"
    out = client.get(url).text
    assert '<option value="up" selected>' in out and '<option value="swings" selected>' in out
    assert '<option value="90" selected>' in out and '<option value="30" selected>' in out
    assert 'name="support" id="support" checked' in out
    assert '<option value="above" selected>' in out and '<option value="below" selected>' in out   # MA50 por encima, EMA9 por debajo
    for bad in ("trend_dir=sideways", "ma200=sometimes", "trend_days=5", "touch=3"):
        assert "Parámetro no válido" in client.get(base + "&" + bad).text, bad


def test_last_price_filter_form(client_and_service):
    client, svc, gw, _ = client_and_service
    base = "/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid"
    r = client.get("/scanner")
    assert 'name="price_min"' in r.text and 'name="price_max"' in r.text
    out = client.get(base + "&price_min=20&price_max=150.5").text
    assert 'name="price_min" placeholder="Mín." value="20"' in out and 'value="150.5"' in out
    assert "Aplicado" in out
    assert "Parámetro no válido" in client.get(base + "&price_min=200&price_max=100").text
    assert "Parámetro no válido" in client.get(base + "&price_min=abc").text
    assert "Parámetro no válido" in client.get(base + "&price_min=-5").text
    only_high = client.get(base + "&price_min=1000").text                  # el subyacente de prueba vale 100: no pasa nada
    assert "Ningún contrato cumple" in only_high or "0 contratos cumplen" in only_high


def test_technical_filters_warn_when_closes_are_missing(client_and_service):
    client, svc, gw, _ = client_and_service
    base = "/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid"
    assert "Faltan cierres diarios" not in client.get(base).text                  # sin filtros técnicos no hace falta histórico
    out = client.get(base + "&ma50=above").text
    assert "Faltan cierres diarios de" in out and "tickers" in out                # el histórico de la prueba está vacío
    svc.bars.upsert("AAPL", [(date(2026, 9, 1), 100.0)])
    assert "Faltan cierres diarios" not in client.get(base + "&ma50=above").text   # con histórico de toda la watchlist, sin aviso


def test_strike_chart_endpoint_and_link(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert 'class="chart-link" data-ticker="AAPL"' in client.get("/scanner").text and 'id="strike-dialog"' in client.get("/scanner").text
    assert "Sin histórico de cierres" in client.get("/chart/strike?ticker=AAPL&strike=75").text
    today = svc.now().date()
    bars = [(today - timedelta(days=700 - i), 100.0 + (i % 40) - (30 if 200 < i < 215 else 0)) for i in range(700)]
    svc.bars.upsert("AAPL", bars)
    last_low = max(d for d, p in bars if p <= 75.0)
    out = client.get("/chart/strike?ticker=AAPL&strike=75").text
    assert "<svg" in out and "STRIKE $75.00" in out and "ACTUAL $" in out
    assert f"hace {(today - last_low).days} días" in out and "meses por encima del strike $75.00" in out
    assert "Sin toques del strike" in client.get("/chart/strike?ticker=AAPL&strike=10").text
    assert client.get("/chart/strike?ticker=AAPL&strike=abc").status_code == 422
    # miniatura de cada fila: imagen SVG propia que se carga al hacerse visible
    page = client.get("/scanner").text
    assert '<img loading="lazy" width="110" height="30" alt="" src="/chart/mini.svg?ticker=AAPL&amp;strike=' in page
    assert "<th data-nosort>Historial</th>" in page
    mini = client.get("/chart/mini.svg?ticker=AAPL&strike=75")
    assert mini.headers["content-type"].startswith("image/svg+xml") and "<path" in mini.text and "stroke-dasharray" in mini.text
    assert "<path" not in client.get("/chart/mini.svg?ticker=NOPE&strike=75").text     # sin histórico: solo un guion


def test_trend_window_months_form(client_and_service):
    client, svc, gw, _ = client_and_service
    base = "/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid"
    r = client.get("/scanner")
    assert 'name="trend_window"' in r.text
    for label in ("1 mes", "2 meses", "3 meses", "6 meses", "9 meses", "12 meses", "18 meses", "24 meses"):
        assert f">{label}</option>" in r.text, label
    assert '<option value="24" selected>' in r.text                                   # por defecto, todo el histórico
    out = client.get(base + "&trend_dir=up&trend_window=9").text
    assert '<option value="9" selected>' in out
    for bad in ("trend_window=4", "trend_window=abc"):
        assert "Parámetro no válido" in client.get(base + "&" + bad).text, bad


def test_moving_average_candles_and_comparisons_form(client_and_service):
    client, svc, gw, _ = client_and_service
    base = "/scanner?submitted=1&discount=10&dte_min=1&dte_max=35&min_yield=0.1&ref=bid"
    r = client.get("/scanner")
    for name in ("ma_frame", "cmp_ema9_ema20", "cmp_ema20_ma50", "cmp_ma50_ma100", "cmp_ma100_ma200"):
        assert f'name="{name}"' in r.text, name
    assert "EMA9 ≥ EMA20" in r.text and "MA100 ≤ MA200" in r.text
    assert 'name="slope_ma50"' in r.text and 'name="slope_ema20"' in r.text and "Ascendente" in r.text
    assert '<option value="up" selected>Ascendente' in client.get(base + "&slope_ma100=up").text
    assert "Parámetro no válido" in client.get(base + "&slope_ma50=gte").text
    out = client.get(base + "&ma_frame=weekly&cmp_ema9_ema20=gte&cmp_ma50_ma100=lte").text
    assert '<option value="weekly" selected>Semanales (semanas)' in out
    assert '<option value="gte" selected>' in out and '<option value="lte" selected>' in out
    # velas semanales / mensuales: las medias que no caben en el histórico no se ofrecen y su valor se descarta
    weekly = client.get(base + "&ma_frame=weekly&ma200=above&slope_ma200=up&cmp_ma100_ma200=gte&ma100=above").text
    assert 'name="ma100"' in weekly and 'name="ma200"' not in weekly
    assert 'name="slope_ma200"' not in weekly and 'name="cmp_ma100_ma200"' not in weekly and 'name="cmp_ma50_ma100"' in weekly
    assert "1 condición" in weekly                                   # solo cuenta MA 100: lo de la MA 200 se descartó
    monthly = client.get(base + "&ma_frame=monthly&ma50=above").text
    for gone in ("ma50", "ma100", "ma200", "slope_ma50", "cmp_ema20_ma50", "cmp_ma50_ma100"):
        assert f'name="{gone}"' not in monthly, gone
    for kept in ("ema9", "ema20", "slope_ema9", "slope_ema20", "cmp_ema9_ema20"):
        assert f'name="{kept}"' in monthly, kept
    assert "Sin condiciones" in monthly
    assert 'name="ma200"' in client.get(base + "&ma_frame=daily").text
    for bad in ("ma_frame=yearly", "cmp_ema9_ema20=above", "cmp_ma50_ma100=equal"):
        assert "Parámetro no válido" in client.get(base + "&" + bad).text, bad
