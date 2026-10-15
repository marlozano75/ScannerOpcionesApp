"""Datos de los filtros de la estrategia: HV30/IV30/cambio de IV con el mercado cerrado, avisos de cobertura, botón que respeta los
demás filtros y reintento de las velas sin respuesta."""
import re
from datetime import timedelta, timezone
from urllib.parse import parse_qs

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.marketdata import tastytrade as tt
from scanner_opciones.marketdata.volatility import FakeVolatility, IVMetrics
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, FixedMarket, make_service, seed_market
from tests.integration.test_web import BASE, client_and_service, refresh  # noqa: F401


async def service_with(volatility, market_open=False):
    _, gw = make_service()
    seed_market(gw)
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(market_open), volatility=volatility)
    await gw.connect()
    svc.watchlist.add(["AAPL"], NOW)
    return svc


FULL = IVMetrics(iv_rank=61.0, iv_percentile=72.0, hv30=20.15, iv30=27.61, iv_change_5d=1.04)


# ---- 2. la IV y la volatilidad histórica no esperan a un refresco de mercado -------------------------------------
async def test_the_iv_stats_are_filled_without_a_market_refresh_and_are_not_requested_again():
    vol = FakeVolatility({"AAPL": FULL})
    svc = await service_with(vol)
    await svc.daily.run_pending()                      # ficha de AAPL (con la IV del lote diario)
    svc.ticker_info.update_iv_stats("AAPL", None, None)
    assert svc.daily.iv_stats_due()                    # nunca bajadas
    vol.calls.clear()
    assert await svc.daily.update_iv_stats(["AAPL"]) == 1
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.hv30, info.iv30, info.iv_change_5d) == (61.0, 20.15, 27.61, 1.04)
    assert vol.calls == [["AAPL"]] and not svc.daily.iv_stats_due()


async def test_a_ticker_the_provider_never_gives_iv30_for_is_retried_only_after_a_few_hours():
    svc = await service_with(FakeVolatility({"AAPL": IVMetrics(iv_rank=10.0, iv_percentile=20.0)}))   # el proveedor no da IV30
    await svc.daily.run_pending()
    await svc.daily.update_iv_stats(["AAPL"])
    assert not svc.daily.iv_stats_due()                # recién intentado: no se repite en cada ciclo
    old = (svc.now().astimezone(timezone.utc) - timedelta(hours=7)).isoformat()
    svc.meta.set("iv_stats_fetched_at", old)
    assert svc.daily.iv_stats_due()


async def test_a_missing_hv30_is_computed_from_the_stored_closes_and_a_missing_value_does_not_discard():
    svc = await service_with(FakeVolatility({}))
    await svc.daily.run_pending()
    svc.ticker_info.update_iv_stats("AAPL", 50.0, 60.0, None, 27.0, None)
    c = svc.criteria(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0)
    assert svc.infos(c)["AAPL"].hv30 is None                       # sin cierres no hay respaldo
    px = 100.0
    days = [svc.now().date() - timedelta(days=i) for i in range(40, 0, -1)]
    svc.bars.upsert("AAPL", [(d, px := px * (1.02 if i % 2 else 0.98)) for i, d in enumerate(days)])
    assert 30 < svc.infos(c)["AAPL"].hv30 < 90 and svc.ticker_info.get("AAPL").hv30 is None    # calculada, no guardada
    assert svc.infos(svc.criteria())["AAPL"].hv30 is None          # sin el filtro no se calcula


async def test_a_provider_failure_keeps_the_stored_values():
    vol = FakeVolatility({"AAPL": FULL})
    svc = await service_with(vol)
    await svc.daily.run_pending()
    vol.error = VolatilityError("sin red")
    assert await svc.daily.update_iv_stats(["AAPL"]) == 0
    assert svc.ticker_info.get("AAPL").hv30 == 20.15


async def test_the_service_startup_fills_the_iv_stats_even_with_the_market_closed():
    vol = FakeVolatility({"AAPL": FULL})
    svc = await service_with(vol, market_open=False)
    await svc.daily.run_pending()
    svc.ticker_info.update_iv_stats("AAPL", None, None)
    await svc._update_iv_stats()
    assert svc.ticker_info.get("AAPL").hv30 == 20.15


async def test_the_daily_update_keeps_hv30_when_the_provider_stops_giving_it():
    vol = FakeVolatility({"AAPL": FULL})
    svc = await service_with(vol)
    await svc.daily.run_pending()
    assert svc.ticker_info.get("AAPL").iv_change_5d == 1.04
    vol.metrics["AAPL"] = IVMetrics(iv_rank=50.0, iv_percentile=60.0)
    await svc.daily.run(["AAPL"])                       # segunda actualización diaria, ya sin HV30 en el proveedor
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.hv30, info.iv30) == (50.0, 20.15, 27.61)


# ---- 4. aviso cuando a un filtro activo le faltan datos --------------------------------------------------------
async def test_data_gap_warnings_name_each_active_filter_with_missing_data():
    svc = await service_with(FakeVolatility({}))
    await svc.daily.run_pending()
    c = svc.criteria(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0, min_atr_multiple=2.0)
    text = " | ".join(svc.filter_data_gaps(c))
    assert "IV/HV30 en 1 de 1 tickers" in text and "cambio de IV a 5 días en 1 de 1" in text and "el ATR en 1 de 1" in text
    assert text.count("no se descartan por este filtro") == 3 and "sus contratos se descartan" not in text
    assert svc.filter_data_gaps(svc.criteria()) == []                      # sin filtros activos no se avisa
    svc.ticker_info.update_iv_stats("AAPL", 50.0, 60.0, 20.0, 27.0, -0.5)
    assert svc.filter_data_gaps(svc.criteria(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0)) == []     # con datos, sin avisos


def test_the_scanner_page_warns_when_an_active_filter_has_no_data(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    page = client.get(BASE + "&use_ivhv=on&ivhv=1.2&use_atr=on&atr_mult=2").text
    assert "Faltan datos para IV/HV30 en 1 de 1 tickers" in page and "Faltan datos para el ATR en 1 de 1" in page
    assert "Faltan datos" not in client.get(BASE).text


# ---- 5. el botón de la estrategia respeta los demás filtros -----------------------------------------------------
def test_loading_a_profile_replaces_the_form_and_saving_stores_the_current_filters(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get(BASE + "&ref=mid&use_oi=on&oi=300&trend_dir=up&price_min=20").text
    href = re.search(r'<a class="preset strategy(?: on)?" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert "use_oi" not in q and "trend_dir" not in q and "price_min" not in q          # el perfil reemplaza todo el formulario
    assert q["profile"] == "Tastytrade" and q["mode"] == "both" and q["dte_min"] == "30"
    on = client.get(href).text
    assert 'class="preset strategy active"' in on and "Tendencia alcista" not in on and "OI ≥ 300" not in on
    # guardar los filtros actuales en un perfil nuevo (solo put) y volver a cargarlo
    saved = client.post("/scanner/profile/save", data={"name": "Mio", "query": BASE.split("?")[1] + "&use_oi=on&oi=300&mode=put&profile=Tastytrade"},
                        follow_redirects=False)
    assert saved.status_code == 303 and "profile=Mio" in saved.headers["location"] and "use_oi=on" in saved.headers["location"]
    mine = client.get(saved.headers["location"]).text
    assert "OI ≥ 300" in mine and ">✓ Mio</a>" in mine and '<option value="spread"' not in mine      # solo los instrumentos activos del perfil
    # perfil inicial: desde /scanner/config; sin consulta se carga ese perfil
    names = [p.name for p in svc.stored_profiles()[0]]
    form = {"n_profiles": str(len(names)), "default": str(names.index("Mio"))}
    for i, n in enumerate(names):
        form.update({f"pf{i}_name": n, f"pf{i}_orig": n, f"pf{i}_put": "on"})
        if n != "Mio":
            form[f"pf{i}_spread"] = "on"
    assert client.post("/scanner/profiles", data=form, follow_redirects=False).status_code == 303
    assert "profile=Mio" in str(client.get("/scanner").url)
    assert "Perfiles del Scanner" in client.get("/scanner/config").text


# ---- 3. velas: los símbolos sin respuesta se piden de nuevo con una conexión nueva -------------------------------
async def test_unanswered_candle_symbols_are_retried_with_a_new_connection(monkeypatch):
    monkeypatch.setattr(tt, "CANDLE_RETRY_WAIT", 0.0)
    calls = []

    async def fake_pass(self, symbols, days, timeout, keep_partial):
        calls.append((list(symbols), keep_partial))
        if len(calls) == 1:
            return {"AAPL": [("d", 1.0, 2.0, 0.5)]}, ["KO", "PBR/A"]          # KO y PBR/A no responden
        if len(calls) == 2:
            return {"KO": [("d", 3.0, 4.0, 2.0)]}, ["PBR/A"]
        return {}, ["PBR/A"]

    monkeypatch.setattr(tt.TastytradeVolatility, "_candles_pass", fake_pass)
    provider = tt.TastytradeVolatility("s", "t")
    out = await provider._sdk_fetch_ohlc(["AAPL", "KO", "PBR/A"], 45, 20.0)
    assert set(out) == {"AAPL", "KO"}
    assert calls == [(["AAPL", "KO", "PBR/A"], False), (["KO", "PBR/A"], False), (["PBR/A"], True)]   # solo lo pendiente; la última pasada conserva lo parcial


async def test_no_retry_when_everything_answers(monkeypatch):
    calls = []

    async def fake_pass(self, symbols, days, timeout, keep_partial):
        calls.append(list(symbols))
        return {s: [("d", 1.0, 1.0, 1.0)] for s in symbols}, []

    monkeypatch.setattr(tt.TastytradeVolatility, "_candles_pass", fake_pass)
    out = await tt.TastytradeVolatility("s", "t")._sdk_fetch_ohlc(["AAPL"], 10, 20.0)
    assert list(out) == ["AAPL"] and len(calls) == 1


# ---- 1. el rango guardado de la configuración de ejemplo cubre la estrategia ---------------------------------------
def test_the_example_config_stores_the_range_the_strategy_needs():
    from pathlib import Path
    from scanner_opciones.config.settings import load_settings
    s = load_settings(Path(__file__).parents[2] / "config" / "config.example.yaml")
    c, st = s.scanner.candidates, s.scanner.strategy
    assert c.strike_below_pct_min <= st.strike_below_pct_min and c.dte_max >= st.dte_max and c.dte_min <= st.dte_min
    assert [(p.name, p.dte_min, p.dte_max) for p in s.scanner.presets] == [("Mensual (TPC)", 25, 35), ("Táctico (TPC)", 10, 14)]


def test_the_two_filter_sets_travel_in_the_url_swap_with_the_tabs_and_are_saved_in_the_profile(client_and_service):
    client, svc, gw, _ = client_and_service
    base = BASE.split("?")[1]
    obase = "&".join("o_" + kv for kv in base.split("&") if not kv.startswith("submitted"))      # el otro juego trae también sus valores básicos
    # juego visible = spread (mode=both y sin set); el de put viaja como o_...
    page = client.get("/scanner?" + base + "&mode=both&use_ns=on&ns=100&set=spread&o_use_oi=on&o_oi=77&" + obase).text
    assert 'name="o_use_oi" value="on"' in page and 'name="o_oi" value="77"' in page and 'data-set="spread"' in page
    assert 'class="settab active" data-set="spread"' in page and 'id="sec-spread"' in page            # el bloque de spread se ve con el juego de spread
    # con el juego de put visible no hay bloque de spread y el de spread viaja oculto
    put_page = client.get("/scanner?" + base + "&mode=both&set=put&use_oi=on&oi=77&o_use_ns=on&o_ns=100&o_use_rr=on&o_rr=5&" + obase).text
    assert 'name="o_use_ns" value="on"' in put_page and 'id="sec-spread"' not in put_page.split('id="scan-form"')[1].split("</aside>")[0] and "Parámetro no válido" not in put_page
    # guardar: cada juego va a su sitio
    r = client.post("/scanner/profile/save", data={"name": "Dos", "query": base + "&mode=both&set=put&use_oi=on&oi=77&o_use_ns=on&o_ns=100&o_use_rr=on&o_rr=5&" + obase},
                    follow_redirects=False)
    assert r.status_code == 303
    prof = next(p for p in svc.stored_profiles()[0] if p.name == "Dos")
    assert prof.put["use_oi"] == "on" and "use_ns" not in prof.put and prof.spread["use_ns"] == "on" and prof.spread["rr"] == "5" and "use_oi" not in prof.spread
    # y al cargarlo (modo Ambos) se ve el juego de put y el de spread viaja oculto
    loaded = client.get(r.headers["location"]).text
    assert 'name="o_use_ns" value="on"' in loaded and 'name="use_oi"' in loaded and "Parámetro no válido" not in loaded
    # guardando desde la pestaña de filtros de SPREAD, al volver se sigue viendo esa pestaña (no la de put)
    sp_query = "&".join(f"{k}={v}" for k, v in {"submitted": "1", "mode": "both", "set": "spread", "discount": "5", "dte_min": "30", "dte_max": "45", "min_yield": "1", "use_rr": "on", "rr": "12", "o_discount": "5", "o_dte_min": "30", "o_dte_max": "45", "o_min_yield": "1"}.items())
    r2 = client.post("/scanner/profile/save", data={"name": "Dos", "query": sp_query}, follow_redirects=False)
    assert r2.status_code == 303 and "set=spread" in r2.headers["location"] and "mode=both" in r2.headers["location"]


def test_profiles_can_be_reordered_coloured_and_duplicated(client_and_service):
    client, svc, gw, _ = client_and_service
    client.get("/scanner?reset=1")
    names = ["Mensual (TPC)", "Táctico (TPC)", "Tastytrade", "Tortuga"]
    form = {"n_profiles": "4", "default": "2"}
    for i, n in enumerate(names):
        form.update({f"pf{i}_name": n, f"pf{i}_orig": n, f"pf{i}_put": "on", f"pf{i}_order": str(10 - i)})     # orden inverso
    form.update({"pf2_usecolor": "on", "pf2_color": "#112233", "pf2_dup": "on", "pf2_spread": "on", "pf0_usecolor": "on", "pf0_color": "rojo"})
    bad = client.post("/scanner/profiles", data=form, follow_redirects=False)
    assert "error=" in bad.headers["location"] and svc.stored_profiles() is None                       # color no válido: no se guarda nada
    form["pf0_color"] = "#aa0000"
    assert "ok=" in client.post("/scanner/profiles", data=form, follow_redirects=False).headers["location"]
    saved, default = svc.stored_profiles()
    assert [p.name for p in saved] == ["Tortuga", "Tastytrade", "Tastytrade (copia)", "Táctico (TPC)", "Mensual (TPC)"]
    by = {p.name: p for p in saved}
    assert by["Tastytrade"].color == "#112233" and by["Tastytrade (copia)"].color == "#112233" and by["Táctico (TPC)"].color == ""
    assert by["Tastytrade (copia)"].put == by["Tastytrade"].put and default == "Tastytrade"
    page = client.get("/scanner?reset=1").text
    assert page.index(">Tortuga</a>") < page.index(">Tastytrade</a>") < page.index(">Mensual (TPC)</a>") and "--pc:#112233" in page
