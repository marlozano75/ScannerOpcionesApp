from datetime import datetime, timedelta

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


def test_watchlist_paste_and_remove(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.post("/watchlist/paste", data={"text": "ko pep AAPL 123"}, follow_redirects=True)
    assert r.status_code == 200 and "2 nuevos, 0 repetidos, 1 rechazados" in r.text
    assert set(svc.watchlist.list()) == {"AAPL", "KO", "PEP"}
    client.post("/watchlist/remove", data={"ticker": "KO"})
    assert "KO" not in svc.watchlist.list()


def test_watchlist_upload_xlsx_and_bad_file(client_and_service):
    client, svc, gw, _ = client_and_service
    import io
    wb = Workbook()
    wb.active.append(["Ticker"])
    wb.active.append(["NVDA"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/watchlist/upload", files={"file": ("w.xlsx", buf.getvalue())}, follow_redirects=True)
    assert "1 nuevos" in r.text and "NVDA" in svc.watchlist.list()
    r = client.post("/watchlist/upload", files={"file": ("w.pdf", b"x")}, follow_redirects=True)
    assert "Error" in r.text


BASE = "/scanner?submitted=1&discount=20&discount_max=40&discount_max=40&dte_min=25&dte_max=35&min_yield=1"


def _optional_block(html: str) -> str:
    return html.split("Filtros opcionales")[1].split("</form>")[0]


def test_scanner_defaults_from_config(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner")
    assert r.status_code == 200 and "contratos cumplen" in r.text and "AAPL" in r.text
    for name, value in (("discount", "10"), ("discount_max", "30"), ("dte_min", "1"), ("dte_max", "35"), ("min_yield", "1")):
        assert f'name="{name}"' in r.text and f'name="{name}" id="{name}" size="{6 if "d" in name[:1] and "dte" not in name else 5}" value="{value}"' in r.text.replace(
            'size="6" value', 'size="6" value').replace('size="5" value', 'size="5" value') or f'value="{value}"' in r.text
    assert "checked" not in _optional_block(r.text)          # ningún filtro opcional marcado


def test_scanner_has_no_operation_selector(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner")
    assert 'name="op"' not in r.text and "<option value=\"tactical\"" not in r.text
    assert 'id="dte_min_label"' not in r.text and 'name="dte_min"' in r.text      # DTE mín. siempre editable
    assert client.get("/scanner?op=tactical").status_code == 200                   # un enlace antiguo no rompe


def test_operation_column_regular_between_25_and_35_dte_otherwise_tactical(client_and_service):
    client, svc, gw, _ = client_and_service
    from datetime import timedelta
    far = OptionContract("AAPL", TODAY + timedelta(days=10), 80.0)
    svc.contracts.sync_for_ticker("AAPL", {svc.contracts.key(c) for c in svc.contracts.list()} | {svc.contracts.key(far)}, [far])
    for c in svc.contracts.list("AAPL"):
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
    refresh(client)
    out = client.get("/scanner?submitted=1&discount=10&discount_max=30&dte_min=1&dte_max=35&min_yield=0.1").text
    assert "<th>Operación</th>" in out
    rows = [row for row in out.split("<tr>") if '<td class="tk">AAPL' in row]
    by_dte = {int(row.split("<td>")[3].split("</td>")[0]): ("Regular" in row, "Táctica" in row) for row in rows}
    assert by_dte == {10: (False, True), 30: (True, False)}          # DTE 10: Táctica; DTE 30: Regular


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
        "/scanner?submitted=1&discount=20&discount_max=40&dte_min=40&dte_max=50&min_yield=1").text
    # descuento mínimo 30 %: el strike a 25 % ya no cumple (solo el de 30 %, si existe)
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&discount=35&discount_max=40&dte_min=25&dte_max=35&min_yield=1").text
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&discount=20&discount_max=40&dte_min=25&dte_max=35&min_yield=50").text
    wide = client.get("/scanner?submitted=1&discount=15&discount_max=40&dte_min=1&dte_max=60&min_yield=0.1")
    assert "contratos cumplen" in wide.text and "yield anual ≥ 0.1%" in wide.text and "DTE 1–60" in wide.text


def test_warning_when_outside_stored_range(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner?submitted=1&discount=2&discount_max=45&dte_min=25&dte_max=90&min_yield=1")
    assert "Descuento mín. por debajo del rango guardado" in r.text and "Descuento máx. por encima del rango guardado" in r.text
    assert "DTE fuera del rango guardado" in r.text
    ok = client.get(BASE)
    assert "Guardado en la actualización diaria" in ok.text and "banner info" not in ok.text.split("</form>")[1].split("<script>")[0]


@pytest.mark.parametrize("qs", [
    "discount=100&discount_max=40&dte_min=25&dte_max=35&min_yield=1",   # descuento fuera de rango
    "discount=20&discount_max=40&dte_min=40&dte_max=35&min_yield=1",    # DTE mín > máx
    "discount=20&discount_max=40&dte_min=25&dte_max=35&min_yield=-1",   # yield negativo
    "discount=&discount_max=40&dte_min=25&dte_max=35&min_yield=1",      # vacío
    "discount=x&discount_max=40&dte_min=25&dte_max=35&min_yield=1",     # no numérico
    "discount=20&discount_max=40&dte_min=2.5&dte_max=35&min_yield=1",   # DTE no entero
    "discount=20&discount_max=40&dte_min=25&dte_max=&min_yield=1",      # DTE máx vacío
    "discount=20&discount_max=10&dte_min=25&dte_max=35&min_yield=1",    # descuento mín > máx
    "discount=20&discount_max=&dte_min=25&dte_max=35&min_yield=1",      # descuento máx vacío
    "discount=20&discount_max=100&dte_min=25&dte_max=35&min_yield=1",   # descuento máx fuera de rango
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


def test_refresh_scoped_quotes_wider_range_then_redirects(client_and_service):
    client, svc, gw, _ = client_and_service
    from datetime import timedelta
    from scanner_opciones.domain.models import OptionQuote
    # contrato guardado con DTE 50: fuera de lo que se cotiza por defecto
    svc.contracts.replace_for_ticker("AAPL", [OptionContract("AAPL", TODAY + timedelta(days=50), 70.0)])
    far = svc.contracts.list("AAPL")[0]
    gw.quotes[far] = OptionQuote(bid=1.0, ask=1.2, open_interest=100)
    refresh(client)
    assert svc.snapshots.all("AAPL") == []                   # el refresco automático no lo cotiza
    data = {"submitted": "1", "discount": "20", "discount_max": "40", "dte_min": "25", "dte_max": "60", "min_yield": "0.5"}
    r = client.post("/scanner/refresh", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/scanner?")
    assert len(svc.snapshots.all("AAPL")) == 1               # ahora sí
    shown = client.get(r.headers["location"])
    assert "contratos cumplen" in shown.text and "1/1 contratos del rango" in shown.text


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


def test_remove_route_deletes_contracts_and_forced_daily_runs_in_background(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert svc.contracts.list("AAPL")
    r = client.post("/watchlist/remove", data={"ticker": "AAPL"}, follow_redirects=True)
    assert "AAPL quitado, con sus contratos" in r.text
    assert svc.contracts.list("AAPL") == [] and svc.snapshots.all("AAPL") == []
    svc.watchlist.add(["AAPL"], NOW)
    r = client.post("/daily", follow_redirects=True)
    assert r.status_code == 200 and "Actualización diaria de 1 tickers" in r.text


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
    assert "<th>Prima ref.</th>" in r.text and "<th>Yield bid</th><th>Yield bid anual</th>" in r.text
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
    url = "/scanner?submitted=1&discount=20&discount_max=40&dte_min=25&dte_max=35&min_yield=15"
    assert "Ningún contrato cumple" in client.get(url + "&ref=bid&ref_x=25").text          # 0.40/75 = 0.53 %
    assert "Ningún contrato cumple" in client.get(url + "&ref=bid_plus_spread&ref_x=25").text   # 0.70/75 = 0.93 %
    assert "contratos cumplen" in client.get(url + "&ref=mid&ref_x=25").text              # 1.00/75 = 1.33 %
    assert "contratos cumplen" in client.get(url + "&ref=bid_plus_spread&ref_x=75").text  # 1.30/75 = 1.73 % con X=75 %
    # la columna «Yield bid» muestra siempre el del bid, aunque el filtro use otra referencia
    assert "0.5%" in client.get(url + "&ref=mid&ref_x=25").text


@pytest.mark.parametrize("qs", [
    "ref=raro&ref_x=25", "ref=bid_plus_spread&ref_x=", "ref=bid_plus_spread&ref_x=abc",
    "ref=bid_plus_spread&ref_x=120", "ref=bid_plus_spread&ref_x=-5",
])
def test_invalid_price_reference(client_and_service, qs):
    client, *_ = client_and_service
    r = client.get(BASE + "&" + qs)
    assert r.status_code == 200 and "Parámetro no válido" in r.text


def test_daily_revalidate_option_is_passed_to_the_service(client_and_service):
    client, svc, gw, _ = client_and_service
    seen = []
    real = svc.run_daily

    async def spy(tickers=None, wait=False, revalidate=False):
        seen.append(revalidate)
        return await real(tickers, wait=wait, revalidate=revalidate)

    svc.run_daily = spy
    client.post("/daily", follow_redirects=False)
    client.post("/daily", data={"revalidate": "1"}, follow_redirects=False)
    client.portal.call(svc.wait_idle)
    assert seen == [False, True]


def test_contracts_page_lists_every_stored_contract_with_scanner_columns(client_and_service):
    client, svc, gw, _ = client_and_service
    from datetime import timedelta
    refresh(client)
    far = OptionContract("AAPL", TODAY + timedelta(days=44), 70.0)     # guardado pero fuera de lo que se cotiza
    svc.contracts.sync_for_ticker("AAPL", {svc.contracts.key(c) for c in svc.contracts.list()} | {svc.contracts.key(far)}, [far])
    r = client.get("/contracts")
    assert r.status_code == 200
    stored = svc.contracts.list()
    assert f"Contratos guardados ({len(stored)})" in r.text
    assert "sin cotizar" in r.text                                       # el de DTE 44 no tiene snapshot
    scan_header = client.get(BASE).text
    for col in ("Bid size", "Prima ref.", "Yield bid anual", "IV Pctl", "Margen ini.", "% cartera si asignado"):
        assert f"<th>{col}</th>" in r.text and f"<th>{col}</th>" in scan_header
    assert 'href="/contracts"' in client.get("/").text                    # enlace en el menú


def test_scanner_links_to_contracts_in_a_new_tab(client_and_service):
    client, *_ = client_and_service
    assert 'href="/contracts" target="_blank"' in client.get("/scanner").text


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


def test_ticker_column_is_sticky_in_scanner_and_contracts(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    scanner = client.get("/scanner").text
    contracts = client.get("/contracts").text
    assert 'class="sortable sticky-tk with-sel" id="scan-results"' in scanner
    assert 'class="sortable sticky-tk" id="stored-contracts"' in contracts
    for page in (scanner, contracts):
        assert '<th class="tk">Ticker</th>' in page and '<td class="tk">AAPL</td>' in page
    assert '<th class="sel"></th>' in scanner and '<td class="sel"><input type="checkbox"' in scanner   # casilla también fija
    assert "table.sticky-tk .tk { left:0;" in scanner and "position:sticky" in scanner


def test_replace_watchlist_from_pasted_text_keeps_common_and_removes_the_rest(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    svc.watchlist.add(["KO", "PEP"], NOW)
    gw.prices["MSFT"] = 300.0
    assert svc.snapshots.all("AAPL") and svc.contracts.list("AAPL")
    r = client.post("/watchlist/paste", data={"text": "aapl msft 123", "mode": "replace"}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"AAPL", "MSFT"}
    assert "Watchlist sustituida: 1 nuevos, 1 conservados, 2 quitados (KO, PEP)" in r.text and "1 rechazados: 123" in r.text
    assert svc.snapshots.all("AAPL") and svc.contracts.list("AAPL")            # el que se queda conserva sus datos
    assert svc.ticker_info.get("KO") is None and svc.contracts.list("KO") == []


def test_add_mode_still_keeps_existing_tickers(client_and_service):
    client, svc, gw, _ = client_and_service
    client.post("/watchlist/paste", data={"text": "KO", "mode": "add"})
    assert set(svc.watchlist.list()) == {"AAPL", "KO"}
    client.post("/watchlist/paste", data={"text": "PEP"})                          # sin modo: añadir
    assert set(svc.watchlist.list()) == {"AAPL", "KO", "PEP"}


def test_replace_with_no_valid_tickers_does_not_wipe_the_watchlist(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.post("/watchlist/paste", data={"text": "123 ??? ", "mode": "replace"}, follow_redirects=True)
    assert svc.watchlist.list() == ["AAPL"]
    assert "ningún ticker válido" in r.text and "no se ha cambiado la watchlist" in r.text
    r = client.post("/watchlist/paste", data={"text": "", "mode": "replace"}, follow_redirects=True)
    assert svc.watchlist.list() == ["AAPL"]


def test_replace_watchlist_from_uploaded_file(client_and_service):
    client, svc, gw, _ = client_and_service
    gw.prices["NVDA"] = 120.0
    r = client.post("/watchlist/upload", data={"mode": "replace"},
                    files={"file": ("w.txt", b"NVDA\nAMD\n")}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"NVDA", "AMD"}
    assert "Watchlist sustituida: 2 nuevos, 0 conservados, 1 quitados (AAPL)" in r.text
    client.post("/watchlist/upload", files={"file": ("w.txt", b"KO")})              # sin modo: añadir
    assert set(svc.watchlist.list()) == {"NVDA", "AMD", "KO"}
    bad = client.post("/watchlist/upload", data={"mode": "replace"}, files={"file": ("w.pdf", b"x")}, follow_redirects=True)
    assert "Error" in bad.text and set(svc.watchlist.list()) == {"NVDA", "AMD", "KO"}


def test_watchlist_page_has_replace_buttons_with_confirmation(client_and_service):
    client, *_ = client_and_service
    page = client.get("/watchlist").text
    assert page.count('name="mode" value="replace"') == 2 and "confirmReplace()" in page
    assert 'name="mode" value="add"' in page


# ---- pestaña RankedStocks (fichero xlsx elegido por el usuario) -----------------------------------

RANK_HEADER = ["Símbolo", "Empresa", "Bolsa", "País", "Capitalización", "Precio", "RS ↓", "Al"]
RANK_ROWS = [
    ["🇺🇸DK", "Delek US Holdings", "NYSE", "US", "$4.4B", "$71.37", "95.6", "Oct 1, 2026"],
    ["🇺🇸KO", "Coca-Cola", "NYSE", "US", "$270.0B", "$60.10", "90.1", "Oct 1, 2026"],
    ["🇺🇸GCT", "GigaCloud", "NASDAQ", "US", "$2.0B", "$52.62", "88.0", "Oct 1, 2026"],
    ["🇺🇸PAYS", "Paysign", "NASDAQ", "US", "$517.1M", "$9.25", "85.6", "Oct 1, 2026"],
]


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


def load_rank(client, **kw):
    return client.post("/rankedstocks/load", files={"file": ("RankedStocks_2026.10.01.xlsx", rank_xlsx(**kw))},
                       follow_redirects=True)


def test_rankedstocks_page_asks_for_a_file_until_one_is_loaded(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/rankedstocks")
    assert r.status_code == 200 and 'name="file"' in r.text and "rankedstocks-table" not in r.text
    assert 'href="/rankedstocks"' in client.get("/").text                      # pestaña en el menú


def test_load_shows_every_column_and_row_of_the_file(client_and_service):
    client, svc, gw, _ = client_and_service
    r = load_rank(client)
    assert "4 filas cargadas de RankedStocks_2026.10.01.xlsx" in r.text
    for col in ("Símbolo", "Empresa", "Bolsa", "País", "Capitalización", "Precio", "RS", "Al", "En watchlist"):
        assert f"<th>{col}</th>" in r.text
    assert "4 de 4 filas" in r.text and "Delek US Holdings" in r.text and "$4.4B" in r.text
    assert 'name="sel" value="DK" checked' in r.text and "🇺🇸" not in r.text     # ticker limpio, sin bandera
    assert 'data-sort="4400000000.0"' in r.text                                   # ordena por valor, muestra el texto
    assert svc.rankedstocks is not None and svc.rankedstocks.source == "RankedStocks_2026.10.01.xlsx"


def test_filters_reduce_rows_and_selection(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    r = client.get("/rankedstocks?c2=NASDAQ")
    assert "2 de 4 filas" in r.text and 'value="GCT" checked' in r.text and 'value="DK"' not in r.text
    r = client.get("/rankedstocks?c2=NYSE&max5=65")
    assert "1 de 4 filas" in r.text and 'value="KO"' in r.text
    r = client.get("/rankedstocks?min4=1B&t1=co")                                  # capitalización ≥ 1B y empresa contiene «co»
    assert "1 de 4 filas" in r.text and 'value="KO"' in r.text
    r = client.get("/rankedstocks?min5=1000")
    assert "Ninguna fila cumple los filtros" in r.text
    bad = client.get("/rankedstocks?min5=abc")
    assert "Filtro no válido" in bad.text and "4 de 4 filas" in bad.text           # no filtra y avisa


def test_apply_add_keeps_existing_and_adds_the_selection(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    gw.prices["GCT"] = 52.0
    r = client.post("/rankedstocks/apply", data={"mode": "add", "sel": ["GCT", "PAYS"]}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"AAPL", "GCT", "PAYS"}
    assert "2 nuevos" in r.text
    assert "✓" in client.get("/rankedstocks").text                                   # la columna «En watchlist» lo marca


def test_apply_replace_swaps_the_whole_watchlist(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    r = client.post("/rankedstocks/apply", data={"mode": "replace", "sel": ["DK", "KO"]}, follow_redirects=True)
    assert set(svc.watchlist.list()) == {"DK", "KO"}
    assert "Watchlist sustituida: 2 nuevos, 0 conservados, 1 quitados (AAPL)" in r.text


def test_apply_with_nothing_selected_does_not_wipe_the_watchlist(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    r = client.post("/rankedstocks/apply", data={"mode": "replace"}, follow_redirects=True)
    assert svc.watchlist.list() == ["AAPL"] and "no se ha cambiado la watchlist" in r.text


def test_page_has_replace_confirmation_and_select_all(client_and_service):
    client, *_ = client_and_service
    page = load_rank(client).text
    assert 'name="mode" value="replace"' in page and "confirmReplace()" in page and 'id="rk-all"' in page


def test_bad_rankedstocks_files_report_an_error_and_keep_the_previous_one(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    bad = client.post("/rankedstocks/load", files={"file": ("w.csv", b"AAPL")}, follow_redirects=True)
    assert "Error" in bad.text and "Formato no soportado" in bad.text
    no_symbol = load_rank(client, header=["Empresa", "Precio"], rows=[["X", "$1"]])
    assert "Error" in no_symbol.text and "Símbolo" in no_symbol.text
    assert svc.rankedstocks is not None and len(svc.rankedstocks.rows) == 4         # sigue el fichero anterior


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

