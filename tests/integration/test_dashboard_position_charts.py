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
    assert 'class="pos-mini"' in page and 'data-pc="1"' in page and "pos-mini-svg" in page          # miniatura junto al ticker
    assert 'id="pc-1"' in page and 'aria-label="Evolución diaria, strikes y vencimientos"' in page   # gráfico grande que se abre al pulsarla
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
    assert 'id="otros"' in page and "perfil «Otros»" in page and "MSFT" in page and "Acción" in page
    assert "Prima capt." in page and "+500" in page           # cobró 2.000, recomprar cuesta 1.500: capturada +500
