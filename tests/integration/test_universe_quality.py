"""Filtros de calidad en la vista del Universo: se filtra ANTES de decidir qué pasa a la watchlist."""
import re
from datetime import date, datetime, timedelta

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.fundamentals import FakeFundamentals, Fundamentals
from tests.integration.test_web import DEFENSIVE, LOWER, client_and_service, load_hello, load_rank  # noqa: F401

# Universo del test: RankedStocks DK KO GCT PAYS + HelloStocks KO ADBE | KO AIG | ACN ALL (AIG y ALL son financieras)
ALL_TICKERS = {"DK", "KO", "GCT", "PAYS", "ADBE", "AIG", "ACN", "ALL"}


def q(ticker, **kw):
    return TickerInfo(ticker, **kw)


def seed(svc):
    svc.quality.save([
        q("DK", eps_ttm=-1.0, positive_quarters=1, reported_quarters=4, market_cap=4e9, option_liquidity=3,
          liabilities_to_equity=2.5, fcf_ttm=-5e6, next_earnings=date(2026, 11, 4)),
        q("KO", eps_ttm=2.5, positive_quarters=4, reported_quarters=4, market_cap=2.7e11, option_liquidity=4,
          liabilities_to_equity=3.5, fcf_ttm=1.2e10, next_earnings=date(2026, 10, 20), eps_surprise_pct=4.0),
        q("GCT", eps_ttm=0.8, positive_quarters=3, reported_quarters=4, market_cap=1.2e9, option_liquidity=2,
          liabilities_to_equity=1.0, fcf_ttm=2e7),
        # PAYS: sin datos de calidad (todavía no descargados)
        q("ADBE", eps_ttm=12.0, positive_quarters=4, reported_quarters=4, market_cap=1.5e11, option_liquidity=4,
          liabilities_to_equity=1.2, fcf_ttm=8e9),
        q("AIG", eps_ttm=-3.0, positive_quarters=2, reported_quarters=4, market_cap=4e10, option_liquidity=3,
          liabilities_to_equity=9.0),                                  # financiera: sin FCF y con mucho pasivo
        q("ACN", eps_ttm=11.0, positive_quarters=4, reported_quarters=4, market_cap=2e11, option_liquidity=4,
          liabilities_to_equity=0.9, fcf_ttm=9e9),
        q("ALL", eps_ttm=15.0, positive_quarters=4, reported_quarters=4, market_cap=5e10, option_liquidity=3,
          liabilities_to_equity=6.0),                                  # financiera
    ])


def shown(page: str) -> set[str]:
    """Tickers con casilla de selección en la tabla del Universo."""
    return set(re.findall(r'name="sel" value="([^"]+)"', page))


def setup(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)
    seed(svc)
    return client, svc


def test_universe_shows_the_quality_indicators_and_the_filter_panel(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe").text
    for header in ("EPS 12 m", "Trim. +", "Cap. (B$)", "Liq. opc.", "Resultados", "Pasivo/Patr.", "FCF (M$)"):
        assert f"<th>{header}</th>" in page
    assert 'id="quality-form"' in page and "Filtros de calidad" in page
    assert shown(page) == ALL_TICKERS and "8 acciones" in page
    ko = [row for row in page.split("<tr>") if 'value="KO"' in row][0]
    assert "3/4" not in ko and "4/4" in ko and "2026-10-20" in ko and "+4 %" in ko     # trimestres, resultados, sorpresa
    assert 'data-sort="2.5"' in ko                                                      # ordenable por EPS
    pays = [row for row in page.split("<tr>") if 'value="PAYS"' in row][0]
    assert "—" in pays                                                                  # sin datos de calidad: guion


def test_negative_values_are_marked_in_red(client_and_service):
    client, svc = setup(client_and_service)
    dk = [row for row in client.get("/universe").text.split("<tr>") if 'value="DK"' in row][0]
    assert dk.count("color:#b3361b") >= 3                                               # EPS ≤ 0, trimestres < 3, FCF ≤ 0


def test_profit_filter_leaves_only_profitable_companies(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1&q_profit=on").text
    assert shown(page) == {"KO", "GCT", "ADBE", "ACN", "ALL"}            # fuera DK y AIG (EPS ≤ 0) y PAYS (sin dato)
    assert "5 acciones" in page and "3 descartadas por calidad (de 8)" in page
    assert 'name="q_profit" checked' in page                              # el formulario conserva el filtro


def test_every_selected_row_is_visible_and_checked(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1&q_profit=on&q_quarters=3").text
    assert shown(page) == {"KO", "GCT", "ADBE", "ACN", "ALL"}
    assert page.count('name="sel"') == page.count('name="sel" value') == len(shown(page))
    assert all("checked" in row for row in page.split("<tr>") if 'name="sel"' in row)


def test_size_liquidity_and_quarters_filters(client_and_service):
    client, svc = setup(client_and_service)
    assert shown(client.get("/universe?submitted=1&q_mcap=2000").text) == {"DK", "KO", "ADBE", "AIG", "ACN", "ALL"}   # GCT 1,2 B$ y PAYS caen
    assert shown(client.get("/universe?submitted=1&q_liq=4").text) == {"KO", "ADBE", "ACN"}
    assert shown(client.get("/universe?submitted=1&q_quarters=4").text) == {"KO", "ADBE", "ACN", "ALL"}


def test_leverage_and_cash_flow_filters_exempt_financial_companies(client_and_service):
    """AIG y ALL son «Financial Services» en el fichero: ni su pasivo/patrimonio ni su falta de FCF los descarta."""
    client, svc = setup(client_and_service)
    lev = shown(client.get("/universe?submitted=1&q_lev=2").text)
    assert lev == {"GCT", "ADBE", "ACN", "AIG", "ALL"}                  # KO (3,5) y DK (2,5) caen; PAYS sin dato
    fcf = shown(client.get("/universe?submitted=1&q_fcf=on").text)
    assert fcf == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}            # DK (FCF < 0) y PAYS (sin dato) caen


def test_filters_combine_and_can_leave_nothing(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1&q_profit=on&q_quarters=4&q_mcap=2000&q_lev=2&q_fcf=on").text
    assert shown(page) == {"ADBE", "ACN", "ALL"}
    assert shown(client.get("/universe?submitted=1&q_liq=4&q_lev=1").text) == {"ACN"}     # liquidez 4 y pasivo/patrimonio ≤ 1


def test_if_no_quality_data_has_been_downloaded_the_filters_leave_nothing(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)                                                   # sin `seed`: aún no hay datos de calidad
    page = client.get("/universe?submitted=1&q_profit=on").text
    assert shown(page) == set() and "ningún ticker cumple los filtros de calidad" in page
    assert "8 descartadas por calidad (de 8)" in page


def test_without_filters_nothing_is_dropped_even_with_missing_data(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1").text
    assert shown(page) == ALL_TICKERS and "descartadas por calidad" not in page


def test_invalid_filter_values_are_rejected_and_nothing_is_filtered(client_and_service):
    client, svc = setup(client_and_service)
    for bad in ("q_mcap=123", "q_quarters=1", "q_liq=9", "q_lev=7", "q_quarters=abc"):
        page = client.get(f"/universe?submitted=1&q_profit=on&{bad}").text
        assert "Parámetro no válido" in page and shown(page) == ALL_TICKERS


def test_filter_applies_inside_a_single_source(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get(f"/universe?src={DEFENSIVE}&submitted=1&q_profit=on").text
    assert shown(page) == {"KO"}                                        # esa pestaña: KO y AIG; AIG tiene EPS ≤ 0


def test_apply_after_filtering_adds_only_the_visible_tickers(client_and_service):
    client, svc = setup(client_and_service)
    visible = shown(client.get("/universe?submitted=1&q_profit=on&q_quarters=4").text)
    assert visible == {"KO", "ADBE", "ACN", "ALL"}
    client.post("/universe/apply", data={"mode": "replace", "sel": sorted(visible)}, follow_redirects=True)
    assert set(svc.watchlist.list()) == visible


def test_missing_edgar_contact_is_explained(client_and_service):
    client, svc = setup(client_and_service)
    assert "edgar.contact" in client.get("/universe").text


# ---- los datos se descargan para todo el Universo, no solo para la watchlist ---------------------------------
def test_quality_scope_is_the_watchlist_plus_the_whole_universe(client_and_service):
    client, svc, gw, _ = client_and_service
    load_hello(client)
    scope = set(svc.quality_scope())
    assert {"KO", "ADBE", "AIG", "ACN", "ALL"} <= scope and "AAPL" in scope      # AAPL: watchlist; el resto: Universo
    svc.manual_tickers.append("ZZZZ")
    assert "ZZZZ" in svc.quality_scope()


def test_loading_a_universe_file_downloads_the_quality_of_its_tickers(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.daily.fundamentals = FakeFundamentals(
        {"ADBE": Fundamentals(12.0, 1.5e11, 4, date(2026, 12, 10), 3.0)}, {"ADBE": [3.0, 3.1, 3.2, 3.3]})
    load_hello(client)
    client.portal.call(svc.wait_idle)
    got = svc.quality.get("ADBE")                  # ADBE no está en la watchlist y ya tiene datos de calidad
    assert got is not None and got.eps_ttm == 12.0 and got.positive_quarters == 4


def test_cleanup_keeps_the_quality_of_the_universe_and_drops_the_rest(client_and_service):
    client, svc, gw, _ = client_and_service
    load_hello(client)
    svc.quality.save([q("ADBE", eps_ttm=1.0), q("AAPL", eps_ttm=2.0), q("GONE", eps_ttm=3.0)])
    removed = svc.cleanup_orphans()
    assert removed["quality"] == 1 and set(svc.quality.all()) == {"ADBE", "AAPL"}      # GONE ya no está en ninguna parte
