from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.models import AccountSummary, OptionContract
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
        # tras el arranque, damos cotizaciones a los contratos candidatos
        return_value = (client, svc, gw, created)
        from scanner_opciones.domain.models import OptionQuote
        for c in svc.contracts.list():
            gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
            gw.margins[c] = 1500.0
        yield return_value


def refresh(client):
    assert client.post("/refresh", follow_redirects=False).status_code == 303


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


BASE = "/scanner?submitted=1&op=regular&discount=20&dte_min=25&dte_max=35&min_yield=1"


def _optional_block(html: str) -> str:
    return html.split("Filtros opcionales")[1].split("</form>")[0]


def test_scanner_defaults_from_config(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner")
    assert r.status_code == 200 and "contratos cumplen" in r.text and "AAPL" in r.text
    assert 'name="discount"' in r.text and 'value="20"' in r.text
    assert 'name="dte_min"' in r.text and 'value="25"' in r.text and 'value="35"' in r.text
    assert "checked" not in _optional_block(r.text)          # ningún filtro opcional marcado


def test_tactical_defaults_and_hidden_dte_min(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner?op=tactical")
    assert 'value="10"' in r.text and 'value="15"' in r.text
    assert 'id="dte_min_label" hidden' in r.text            # solo DTE máx. visible
    reg = client.get("/scanner?op=regular")
    assert 'id="dte_min_label" hidden' not in reg.text


def test_tactical_ignores_dte_min_from_form(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    r = client.get("/scanner?submitted=1&op=tactical&discount=10&dte_min=99&dte_max=15&min_yield=1")
    assert "DTE 1–15" in r.text                             # el mínimo sale de la configuración


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
        "/scanner?submitted=1&op=regular&discount=20&dte_min=40&dte_max=50&min_yield=1").text
    # descuento mínimo 30 %: el strike a 25 % ya no cumple (solo el de 30 %, si existe)
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&op=regular&discount=44&dte_min=25&dte_max=35&min_yield=1").text
    assert "Ningún contrato cumple" in client.get(
        "/scanner?submitted=1&op=regular&discount=20&dte_min=25&dte_max=35&min_yield=50").text
    wide = client.get("/scanner?submitted=1&op=regular&discount=15&dte_min=1&dte_max=60&min_yield=0.1")
    assert "contratos cumplen" in wide.text and "yield bruto ≥ 0.1%" in wide.text and "DTE 1–60" in wide.text


def test_warning_when_outside_stored_range(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.get("/scanner?submitted=1&op=regular&discount=5&dte_min=25&dte_max=90&min_yield=1")
    assert "Descuento por debajo del rango guardado" in r.text and "DTE fuera del rango guardado" in r.text
    ok = client.get(BASE)
    assert "Guardado en la actualización diaria" in ok.text and "banner info" not in ok.text.split("</form>")[1].split("<script>")[0]


@pytest.mark.parametrize("qs", [
    "discount=100&dte_min=25&dte_max=35&min_yield=1",   # descuento fuera de rango
    "discount=20&dte_min=40&dte_max=35&min_yield=1",    # DTE mín > máx
    "discount=20&dte_min=25&dte_max=35&min_yield=-1",   # yield negativo
    "discount=&dte_min=25&dte_max=35&min_yield=1",      # vacío
    "discount=x&dte_min=25&dte_max=35&min_yield=1",     # no numérico
    "discount=20&dte_min=2.5&dte_max=35&min_yield=1",   # DTE no entero
    "discount=20&dte_min=25&dte_max=&min_yield=1",      # DTE máx vacío
])
def test_invalid_form_values(client_and_service, qs):
    client, *_ = client_and_service
    r = client.get("/scanner?submitted=1&op=regular&" + qs)
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
    data = {"submitted": "1", "op": "regular", "discount": "20", "dte_min": "25", "dte_max": "60", "min_yield": "0.5"}
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
