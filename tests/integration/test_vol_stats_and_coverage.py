"""Datos de los filtros de la estrategia: HV30/IV30/cambio de IV con el mercado cerrado, avisos de cobertura, botón que respeta los
demás filtros y reintento de las velas sin respuesta."""
import re
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


async def test_the_iv_stats_are_due_again_if_a_ticker_still_has_no_hv30():
    svc = await service_with(FakeVolatility({"AAPL": IVMetrics(iv_rank=10.0, iv_percentile=20.0)}))   # el proveedor no da HV30
    await svc.daily.run_pending()
    await svc.daily.update_iv_stats(["AAPL"])
    assert svc.daily.iv_stats_due()                    # sigue sin HV30: se vuelve a intentar


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
def test_the_strategy_button_keeps_the_filters_that_are_not_part_of_the_strategy(client_and_service):
    client, svc, gw, _ = client_and_service
    url = BASE + "&ref=mid&use_oi=on&oi=300&trend_dir=up&price_min=20&dte_min=5&use_near=on&near_pct=9&use_boll=on&boll_pctb=0.1"
    page = client.get(url).text
    href = re.search(r'<a class="strategy-btn(?: on)?" href="(/scanner\?[^"]+)"', page).group(1).replace("&amp;", "&")
    q = {k: v[0] for k, v in parse_qs(href.split("?")[1]).items()}
    assert (q["ref"], q["use_oi"], q["oi"], q["trend_dir"], q["price_min"]) == ("mid", "on", "300", "up", "20")     # se conservan
    assert q["dte_min"] == "30" and q["near_pct"] == "3" and "use_boll" not in q and "boll_pctb" not in q               # la estrategia manda
    on = client.get(href).text
    assert 'class="strategy-btn on"' in on and "Tendencia alcista" in on and "OI ≥ 300" in on
    first = client.get("/scanner?reset=1").text                                                                         # sin consulta previa: solo la estrategia
    href0 = re.search(r'<a class="strategy-btn" href="(/scanner\?[^"]+)"', first).group(1).replace("&amp;", "&")
    assert "trend_dir" not in href0 and "use_oi" not in href0


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
    assert s.scanner.presets[2].dte_max == 35                                             # Largo plazo conserva su 16–35
