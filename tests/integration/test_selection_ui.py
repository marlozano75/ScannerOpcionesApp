"""Selección de tickers editable en /scanner/config: vista previa sin efectos, confirmación al sacar tickers, guardado persistente y restaurar."""
from datetime import timedelta

from scanner_opciones.scanner.premium import PremiumRow
from tests.integration.test_web import NOW, client_and_service  # noqa: F401  (fixture)

FORM = {"f_window_days": "10", "f_min_days": "5", "f_min_hits": "3", "f_warm_ratio": "60", "f_quarantine_days": "14",
        "f_put_min_option_liquidity": "2", "f_spread_min_option_liquidity": "3", "f_cds_min_option_liquidity": "3",
        "f_max_spread_pct": "20", "f_min_oi": "50", "f_margin_max_per_cycle": "800", "warm_once": "on"}


def _bad_history(svc, ticker="AAPL"):
    profile = next(iter(svc._selection_targets()))        # un perfil guardado cualquiera (0,1 % frente a su mínimo)
    for n in range(6):
        svc.premium_repo.record_day((NOW - timedelta(days=n + 1)).date(), [PremiumRow(ticker, profile, 3, 0, 0.1, None, None, 10.0, 300)], 365)


def test_the_page_shows_the_selection_section_with_current_values(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner/config").text
    assert "Selección de tickers" in page and 'name="f_min_hits" value="3"' in page and 'name="f_warm_ratio" value="60.0"' in page
    assert 'name="f_margin_max_per_cycle" value="800"' in page and "Perfiles a los que apunta cada fuente" in page


def test_preview_changes_nothing_and_reports_the_effect(client_and_service):
    client, svc, gw, _ = client_and_service
    _bad_history(svc)
    svc.meta.set("watchlist_sync", "on")
    r = client.post("/scanner/selection", data={**FORM, "enabled": "on", "action": "preview"})
    assert r.status_code == 200 and "Vista previa" in r.text and "no se ha aplicado nada" in r.text
    assert "1 en cuarentena" in r.text.replace("<b>", "").replace("</b>", "") and "Saldrían de la Watchlist" in r.text
    assert svc.settings.scanner.selection.enabled is False and svc.watchlist.list() == ["AAPL"] and svc.ticker_selection() == {}
    assert svc.meta.get("selection_user_config") in (None, "")


def test_preview_without_enough_history_says_nothing_would_be_discarded(client_and_service):
    client, svc, gw, _ = client_and_service
    text = client.post("/scanner/selection", data={**FORM, "enabled": "on", "action": "preview"}).text
    assert "Todavía no hay historial suficiente" in text


def test_saving_something_that_removes_tickers_needs_confirmation(client_and_service):
    client, svc, gw, _ = client_and_service
    _bad_history(svc)
    svc.meta.set("watchlist_sync", "on")
    r = client.post("/scanner/selection", data={**FORM, "enabled": "on", "action": "save"})
    assert r.status_code == 200 and "Confirmar y guardar" in r.text and svc.settings.scanner.selection.enabled is False
    r = client.post("/scanner/selection", data={**FORM, "enabled": "on", "action": "save", "confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert svc.settings.scanner.selection.enabled is True and svc.ticker_selection()["AAPL"]["state"] == "quarantine"


def test_saved_values_apply_at_once_survive_a_restart_and_can_be_reset(client_and_service):
    client, svc, gw, _ = client_and_service
    data = {**FORM, "f_min_hits": "4", "f_margin_max_per_cycle": "300", "f_spread_min_option_liquidity": "4", "action": "save"}
    r = client.post("/scanner/selection", data=data, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert (svc.settings.scanner.selection.min_hits, svc.settings.refresh.margin_max_per_cycle, svc.settings.scanner.ticker_modes.spread_min_option_liquidity) == (4, 300, 4)
    assert svc.refresh_job.settings.refresh.margin_max_per_cycle == 300            # los jobs ven lo mismo que el servicio
    svc._apply_selection_config(svc._build_selection_config(svc._yaml_selection))       # «reinicio»: vuelve lo de config.yaml...
    assert svc.settings.scanner.selection.min_hits == 3
    svc._load_selection_config()                                                         # ...y al cargar manda lo guardado
    assert svc.settings.scanner.selection.min_hits == 4 and svc.settings.refresh.margin_max_per_cycle == 300
    client.post("/scanner/selection", data={"action": "reset"}, follow_redirects=False)
    assert svc.settings.scanner.selection.min_hits == 3 and svc.settings.refresh.margin_max_per_cycle == 800 and not svc.meta.get("selection_user_config")


def test_invalid_values_are_reported_and_nothing_is_saved(client_and_service):
    client, svc, gw, _ = client_and_service
    r = client.post("/scanner/selection", data={**FORM, "f_min_hits": "20", "action": "save"})       # más aciertos que sesiones de ventana
    assert r.status_code == 200 and "window_days" in r.text.replace("min_days y min_hits no pueden superar window_days", "window_days")
    assert svc.settings.scanner.selection.min_hits == 3 and not svc.meta.get("selection_user_config")
    r = client.post("/scanner/selection", data={**FORM, "f_min_oi": "abc", "action": "save"})
    assert r.status_code == 200 and "no es un número válido" in r.text


def test_source_profiles_table_is_saved(client_and_service):
    client, svc, gw, _ = client_and_service
    sources, profiles = svc.selection_sources_and_profiles()
    svc.manual_sources["Mi fuente"] = ["AAPL"]
    sources, profiles = svc.selection_sources_and_profiles()
    i, j = sources.index("Mi fuente"), 0
    client.post("/scanner/selection", data={**FORM, f"sp_{i}_{j}": "on", "action": "save"}, follow_redirects=False)
    assert svc.settings.scanner.selection.source_profiles == {"Mi fuente": [profiles[0]]}
