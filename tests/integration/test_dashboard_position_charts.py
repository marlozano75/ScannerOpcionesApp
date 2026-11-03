"""El Panel dibuja un gráfico por ticker con puts vendidas: cierres diarios, strike, vencimiento y revisión a 21 DTE."""
from datetime import timedelta

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionContract, Position
from tests.integration.test_service import NOW, TODAY
from tests.integration.test_web import client_and_service, refresh  # noqa: F401


def test_dashboard_shows_a_thumbnail_next_to_each_ticker_that_opens_the_full_chart(client_and_service):
    client, svc, gw, _ = client_and_service
    gw.positions = [Position("AAPL", -2, -1_500.0, "Technology", OptionContract("AAPL", TODAY + timedelta(days=35), 90.0, OptionRight.PUT), avg_cost=300.0)]
    svc.bars.upsert("AAPL", [(TODAY - timedelta(days=40 - i), 100.0 + i * 0.1) for i in range(41)])
    refresh(client)
    page = client.get("/").text
    assert 'class="pos-mini"' in page and 'data-key="AAPL|' in page and "pos-mini-svg" in page      # miniatura junto al ticker
    assert 'class="pcw-view"' not in page and len(page) < 400_000                                          # el gráfico grande ya no se incrusta: se pide al pulsar
    key = page.split('data-key="', 1)[1].split('"', 1)[0]
    page = client.get("/management/chart", params={"key": key}).text                              # gráfico grande que se abre al pulsarla
    assert 'aria-label="Evolución diaria, strikes y vencimientos"' in page and 'class="pcw-view"' in page
    assert "strike 90" in page and "venc. " in page
    assert "revisar " in page or "21 DTE " in page          # según el perfil con el que se reconozca la put, «revisar» (Tastytrade) o la referencia (TPC)


def test_dashboard_shows_portfolio_status_others_and_captured_premium(client_and_service):
    client, svc, gw, _ = client_and_service
    gw.positions = [Position("AAPL", -2, -1_500.0, "Technology", OptionContract("AAPL", TODAY + timedelta(days=35), 90.0, OptionRight.PUT), avg_cost=1_000.0),
                    Position("MSFT", 10, 4_000.0, "Technology", avg_cost=350.0)]
    refresh(client)
    page = client.get("/").text
    assert "Estado de la cartera" in page and "Riesgo total abierto" in page and "Margen comprometido" in page
    assert "Cambio diario" not in page and "P&amp;L latente" not in page
    assert 'id="gestion"' in page and "Gestión de posiciones" in page and "Otras posiciones" not in page
    assert "MSFT" in page and "Acción" in page                 # la acción convive con la put en la misma tabla
    assert "P&amp;L ($)" in page and "+500" in page            # cobró 2.000, recomprar cuesta 1.500: P&L (capturada) +500


def test_dashboard_gives_non_put_positions_zone_alert_and_row_urgency(client_and_service):
    client, svc, gw, _ = client_and_service
    exp = TODAY + timedelta(days=35)
    gw.positions = [Position("AAPL", 1, 80.0, "Technology", OptionContract("AAPL", exp, 100.0, OptionRight.CALL), avg_cost=200.0)]    # call comprada: −60 %
    refresh(client)
    page = client.get("/").text
    assert 'class="u1"' not in page and "En pérdida" not in page    # MTO es TPC aquí (mantiene hasta el vencimiento): sin stop ni aviso de pérdida
    assert 'class="chip z-' in page and "Δ" in page         # chip de zona con la delta (estimada o real)


def test_management_chart_of_an_unknown_position_is_404(client_and_service):
    client, *_ = client_and_service
    assert client.get("/management/chart", params={"key": "ZZZ|2030-01-01|1|None"}).status_code == 404
