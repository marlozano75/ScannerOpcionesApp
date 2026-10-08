"""Universo: filtros de solvencia con grado de exigencia, indicadores opcionales y sectores exentos."""
import re
import sqlite3

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.financials import NO_LIMIT
from tests.integration.test_universe_quality import ALL_TICKERS, shown
from tests.integration.test_web import client_and_service, load_hello, load_rank  # noqa: F401

# Universo: DK GCT PAYS (sin sector) · KO (Consumer Defensive) · ADBE ACN (Technology) · AIG ALL (Financial Services → exentas)
DATA = {
    "DK":   dict(debt_to_equity=0.4, interest_coverage=2.5, cash_to_short_debt=0.8, ocf_to_debt=0.20, capex_to_ocf=0.50, fcf_to_assets=0.02, net_buyback_pct=-1.0),
    "KO":   dict(debt_to_equity=1.2, interest_coverage=8.0, cash_to_short_debt=1.5, ocf_to_debt=0.40, capex_to_ocf=0.10, fcf_to_assets=0.09, net_buyback_pct=0.5),
    "GCT":  dict(debt_to_equity=0.9, interest_coverage=NO_LIMIT, cash_to_short_debt=NO_LIMIT, ocf_to_debt=NO_LIMIT, capex_to_ocf=0.30, fcf_to_assets=0.13, net_buyback_pct=2.5),
    "ADBE": dict(debt_to_equity=0.2, interest_coverage=NO_LIMIT, cash_to_short_debt=3.0, ocf_to_debt=1.20, capex_to_ocf=0.05, fcf_to_assets=0.20, net_buyback_pct=3.0),
    "ACN":  dict(debt_to_equity=0.05, interest_coverage=4.0, cash_to_short_debt=2.5, ocf_to_debt=0.90, capex_to_ocf=0.15, fcf_to_assets=0.10, net_buyback_pct=1.2),
    "AIG":  dict(),                                                  # exenta: sin datos de deuda ni de caja
    "ALL":  dict(debt_to_equity=4.0, interest_coverage=0.5),         # exenta: mala solvencia que no se mide
    # PAYS: sin datos de calidad
}


def setup(client_and_service):
    client, svc, gw, _ = client_and_service
    load_rank(client)
    load_hello(client)
    svc.quality.save([TickerInfo(t, **v) for t, v in DATA.items()])
    return client, svc


def filtered(client, query):
    return shown(client.get("/universe?submitted=1&" + query).text)


def test_the_form_offers_the_levels_with_their_thresholds(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe").text
    for name in ("q_de", "q_cov", "q_cash", "q_ocfd", "q_capex", "q_fcfa", "q_bb"):
        assert f'name="{name}"' in page
    assert 'id="q_solv_master"' in page
    assert "Flexible (≤ 1.5)" in page and "Estándar (≥ 3×)" in page and "Estricto (≥ 5×)" in page
    assert "Estándar (≥ 30 %)" in page and "Estricto (≤ 20 %)" in page and "Estricto (≥ 2 %)" in page
    assert "Opcionales" in page


def test_debt_to_equity_gets_stricter_with_each_level(client_and_service):
    client, svc = setup(client_and_service)
    flexible = filtered(client, "q_de=flexible")        # ≤ 1,5
    standard = filtered(client, "q_de=standard")        # ≤ 1,0
    strict = filtered(client, "q_de=strict")            # ≤ 0,5
    assert flexible == {"DK", "KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}      # PAYS (sin dato) cae; las exentas pasan
    assert standard == {"DK", "GCT", "ADBE", "ACN", "AIG", "ALL"}
    assert strict == {"DK", "ADBE", "ACN", "AIG", "ALL"}
    assert strict < standard < flexible


def test_interest_coverage_levels(client_and_service):
    client, svc = setup(client_and_service)
    assert filtered(client, "q_cov=flexible") == {"DK", "KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}     # ≥ 2×
    assert filtered(client, "q_cov=standard") == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}           # ≥ 3×
    assert filtered(client, "q_cov=strict") == {"KO", "GCT", "ADBE", "AIG", "ALL"}                    # ≥ 5×


def test_cash_and_operating_cash_flow_over_debt_levels(client_and_service):
    client, svc = setup(client_and_service)
    assert filtered(client, "q_cash=standard") == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}           # efectivo ≥ 1× la deuda corriente
    assert filtered(client, "q_cash=strict") == {"ADBE", "ACN", "GCT", "AIG", "ALL"}                   # ≥ 2×; GCT sin deuda
    assert filtered(client, "q_ocfd=standard") == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}           # flujo operativo ≥ 30 % de la deuda
    assert filtered(client, "q_ocfd=strict") == {"GCT", "ADBE", "ACN", "AIG", "ALL"}                   # ≥ 50 %


def test_optional_indicators_are_filters_too_when_chosen(client_and_service):
    client, svc = setup(client_and_service)
    assert filtered(client, "q_capex=standard") == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}          # capex < 35 % del flujo operativo
    assert filtered(client, "q_capex=strict") == {"KO", "ADBE", "ACN", "AIG", "ALL"}                   # < 20 %
    assert filtered(client, "q_fcfa=strict") == {"GCT", "ADBE", "AIG", "ALL"}                          # FCF/activos ≥ 12 %
    assert filtered(client, "q_bb=strict") == {"GCT", "ADBE", "AIG", "ALL"}                            # recompra neta ≥ 2 %
    assert filtered(client, "q_bb=flexible") == {"KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}             # ≥ 0 %: sin dilución


def test_without_a_level_nothing_is_filtered(client_and_service):
    client, svc = setup(client_and_service)
    assert filtered(client, "q_de=&q_cov=&q_cash=&q_ocfd=&q_capex=") == ALL_TICKERS


def test_the_four_solvency_filters_together_at_the_same_level(client_and_service):
    """Es lo que hace el selector maestro: el mismo grado en los cuatro."""
    client, svc = setup(client_and_service)
    assert filtered(client, "q_de=standard&q_cov=standard&q_cash=standard&q_ocfd=standard") == {"GCT", "ADBE", "ACN", "AIG", "ALL"}
    assert filtered(client, "q_de=strict&q_cov=strict&q_cash=strict&q_ocfd=strict") == {"ADBE", "AIG", "ALL"}
    assert filtered(client, "q_de=flexible&q_cov=flexible&q_cash=flexible&q_ocfd=flexible") == {"DK", "KO", "GCT", "ADBE", "ACN", "AIG", "ALL"}


def test_solvency_combines_with_the_other_quality_filters(client_and_service):
    client, svc = setup(client_and_service)
    svc.quality.save([TickerInfo(t, eps_ttm=1.0, **v) for t, v in DATA.items() if t != "ACN"])
    assert filtered(client, "q_profit=on&q_de=standard") == {"DK", "GCT", "ADBE", "AIG", "ALL"}   # ACN ya no tiene EPS; las exentas solo se libran de la solvencia


def test_exempt_sectors_pass_without_being_measured_and_show_na(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1&q_de=strict&q_cov=strict&q_cash=strict&q_ocfd=strict").text
    aig = [row for row in page.split("<tr>") if 'value="AIG"' in row][0]
    assert "n/a" in aig and aig.count("n/a") >= 6                          # sin datos pero exenta: «n/a», no «—»
    all_ = [row for row in page.split("<tr>") if 'value="ALL"' in row][0]
    assert 'data-sort="4.0"' in all_ or 'data-sort="4"' in all_            # ALL tiene deuda/patr. 4 y pasa: no se mide


def test_no_data_is_a_dash_and_no_limit_an_infinity_sign(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe").text
    pays = [row for row in page.split("<tr>") if 'value="PAYS"' in row][0]
    assert "n/a" not in pays and "—" in pays                                # PAYS no es exenta: sin datos = «—»
    gct = [row for row in page.split("<tr>") if 'value="GCT"' in row][0]
    assert "∞" in gct                                                      # sin deuda: nada que cubrir
    adbe = [row for row in page.split("<tr>") if 'value="ADBE"' in row][0]
    assert "120%" in adbe and "3.00×" in adbe                              # flujo operativo/deuda y efectivo/deuda corriente


def test_the_new_columns_are_there_and_sortable(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe").text
    for header in ("Deuda/Patr.", "Cobertura int.", "Efectivo/Deuda CP", "Flujo op./Deuda", "CapEx/Flujo op.", "FCF/Activos", "Recompra neta"):
        assert f"<th>{header}</th>" in page
    ko = [row for row in page.split("<tr>") if 'value="KO"' in row][0]
    assert 'data-sort="1.2"' in ko and 'data-sort="8.0"' in ko


def test_invalid_level_is_rejected_and_nothing_is_filtered(client_and_service):
    client, svc = setup(client_and_service)
    for bad in ("q_de=ultra", "q_cov=STANDARD", "q_bb=1.5"):
        page = client.get(f"/universe?submitted=1&{bad}").text
        assert "grado de exigencia no permitido" in page and shown(page) == ALL_TICKERS


def test_the_scanner_does_not_get_the_solvency_selectors(client_and_service):
    client, svc = setup(client_and_service)
    scanner = client.get("/scanner").text
    assert 'name="q_de"' not in scanner and 'name="q_cov"' not in scanner
    assert 'name="q_liq"' in scanner                                       # el Scanner conserva los suyos


def test_the_exemption_list_comes_from_the_configuration(client_and_service):
    client, svc = setup(client_and_service)
    svc.settings = svc.settings.model_copy(update={"scanner": svc.settings.scanner.model_copy(update={
        "quality": svc.settings.scanner.quality.model_copy(update={"exempt_sectors": ["consumer"]})})})
    strict = filtered(client, "q_de=strict")
    assert "AIG" not in strict and "ALL" not in strict                     # ya no son exentas (sin datos / 4,0 > 0,5)
    assert "KO" in strict                                                  # Consumer Defensive: exenta por la nueva lista


def test_filter_text_in_the_results_header(client_and_service):
    client, svc = setup(client_and_service)
    page = client.get("/universe?submitted=1&q_de=strict").text
    assert re.search(r"(\d+) descartadas por calidad \(de 8\)", page)


# ---- migración v16: se vuelve a consultar EDGAR para rellenar las columnas nuevas ----------------------------
def test_migration_v16_adds_the_columns_and_forces_a_new_edgar_pass(tmp_path):
    from scanner_opciones.storage.db import MIGRATIONS, Database
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    for version, script in enumerate(MIGRATIONS[:15], start=1):
        raw.executescript(script)
        raw.execute(f"PRAGMA user_version = {version}")
    raw.execute("INSERT INTO ticker_quality (ticker, eps_ttm, financials_at, fcf_ttm) VALUES ('AAPL', 8.7, '2026-10-01T10:00:00', 1e9)")
    raw.commit()
    raw.close()
    db = Database(path)
    row = db.conn.execute("SELECT * FROM ticker_quality WHERE ticker = 'AAPL'").fetchone()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 16
    assert row["eps_ttm"] == 8.7 and row["fcf_ttm"] == 1e9                  # no se pierde nada
    assert row["financials_at"] is None and row["debt_to_equity"] is None   # EDGAR se vuelve a consultar
    assert {"debt_to_equity", "interest_coverage", "cash_to_short_debt", "ocf_to_debt", "capex_to_ocf", "fcf_to_assets",
            "net_buyback_pct"} <= set(row.keys())
