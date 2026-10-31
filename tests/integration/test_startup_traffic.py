"""Peticiones a tastytrade al abrir la aplicación con el mercado cerrado: la precarga de las ideas ya no se hace (se cotizan al abrir la pestaña
y, cerrado el mercado, una sola vez tras el cierre), el VIX guardado evita pedirlo de nuevo y los símbolos sin velas no abren conexiones cada vez."""
import json
from datetime import date, datetime

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import OptionQuote, VixData
from scanner_opciones.marketdata import tastytrade as tt
from scanner_opciones.storage.db import Database
from tests.integration.test_web import client_and_service  # noqa: F401

NOW = datetime(2026, 10, 17, 12, 0)       # sábado: mercado cerrado


def ideas_payload():
    return json.dumps({"version": 1, "generated_at": "2026-10-16T13:00:00", "tickers": [{"ticker": "MPC", "month_traders": 5, "month_operations": 9}],
                       "ideas": [{"date": "2026-10-15", "trader": "ana", "ticker": "MPC", "strategy": "Short Put", "expiry": "2026-11-20",
                                  "short_strike": 440, "long_strike": None, "contracts": 1, "credit": 5.0, "max_loss": None}]})


class CountingProvider:
    """Hace de proveedor de tastytrade y cuenta cuántas veces se le pide algo."""
    def __init__(self):
        self.calls = []

    async def get_put_listing(self, ticker):
        self.calls.append("chain")
        return {(date(2026, 11, 20), 440.0): "MPC  261120P00440000"}

    async def get_option_quotes(self, symbols):
        self.calls.append("quotes")
        return {s: OptionQuote(bid=4.0, ask=4.4) for s in symbols}

    async def get_prices(self, tickers):
        self.calls.append("prices")
        return {t: 476.0 for t in tickers}

    async def get_daily_closes(self, tickers, days, timeout=20.0):
        self.calls.append("closes")
        return {}

    async def get_iv_metrics(self, tickers):
        self.calls.append("metrics")
        return {}


def make(tmp_path):
    path = tmp_path / "tortuga.json"
    path.write_text(ideas_payload(), encoding="utf-8")
    settings = Settings.model_validate({"community": {"path": str(path)}})
    gw = FakeGateway()
    gw.connected = True
    provider = CountingProvider()
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW, volatility=provider)
    svc._has_options["MPC"] = True
    return svc, provider


async def test_syncing_the_community_does_not_quote_the_ideas_any_more(tmp_path):
    svc, provider = make(tmp_path)
    svc.sync_community_in_background()
    await svc.wait_idle()
    assert provider.calls == []                                             # ni cadenas, ni precios, ni velas al arrancar


async def test_with_the_market_closed_the_ideas_are_quoted_once_after_the_close(tmp_path):
    svc, provider = make(tmp_path)
    await svc.refresh_community_quotes()                                    # al abrir la pestaña Ideas
    first = list(provider.calls)
    assert "chain" in first and "quotes" in first and "prices" in first
    await svc.refresh_community_quotes(0)                                   # otra visita (aunque la edad máxima sea 0): ya son de después del cierre
    assert provider.calls == first


async def test_the_saved_vix_is_restored_and_not_requested_again_when_the_market_is_closed(tmp_path):
    settings = Settings()
    gw = FakeGateway()
    gw.connected = True
    gw.vix = VixData(21.5, [(date(2026, 10, 15), 20.0), (date(2026, 10, 16), 21.5)], [(date(2026, 11, 18), 22.4)], datetime(2026, 10, 17, 11, 0))
    db = Database(tmp_path / "app.db")
    first = AppService(gw, db, settings, lambda: NOW)
    await first._refresh_vix()
    assert first.state.vix.current == 21.5
    calls = []
    original = gw.get_vix_data

    async def counting(*a, **kw):
        calls.append(1)
        return await original(*a, **kw)

    gw.get_vix_data = counting
    second = AppService(gw, db, settings, lambda: NOW)                      # un arranque nuevo con la misma base de datos
    assert second.state.vix is not None and second.state.vix.current == 21.5 and second.state.vix.futures == [(date(2026, 11, 18), 22.4)]
    await second._refresh_vix(skip_closed=True)
    assert calls == []                                                      # mercado cerrado y el VIX guardado es posterior al cierre


async def test_a_saved_scanner_config_with_a_retired_field_is_still_loaded(tmp_path):
    """Un campo que se quitó del modelo (p. ej. `max_short_spread_pct`) invalidaba toda la configuración guardada en cada arranque."""
    db = Database(tmp_path / "app.db")
    first = AppService(FakeGateway(), db, Settings(), lambda: NOW)
    first.save_scanner_config([p.model_dump() for p in first.settings.scanner.presets], first.settings.scanner.strategy.model_dump(),
                              {**first.settings.scanner.spreads.model_dump(), "min_credit_usd": 77.0})
    stored = json.loads(first.meta.get("scanner_user_config"))
    stored["spreads"]["max_short_spread_pct"] = 25.0                         # clave de una versión anterior
    first.meta.set("scanner_user_config", json.dumps(stored))
    second = AppService(FakeGateway(), db, Settings(), lambda: NOW)
    assert second.settings.scanner.spreads.min_credit_usd == 77.0            # se aplicó lo guardado en vez de volver a config.yaml


async def test_symbols_without_candles_are_not_asked_again_for_a_while(monkeypatch):
    monkeypatch.setattr(tt, "CANDLE_RETRY_WAIT", 0.0)
    provider = tt.TastytradeVolatility("id", "token")
    passes = []

    async def silent(symbols, days, timeout, keep_partial):
        passes.append(list(symbols))
        return {}, list(symbols)                                            # nadie responde

    monkeypatch.setattr(provider, "_candles_pass", silent)
    assert await provider._sdk_fetch_ohlc(["RUTW"], 30, 1.0) == {}
    assert len(passes) == 1 + tt.CANDLE_RETRIES                             # primera vez: la pasada y sus reintentos
    assert await provider._sdk_fetch_ohlc(["RUTW", "AAPL"], 30, 1.0) == {}
    assert all("RUTW" not in p for p in passes[1 + tt.CANDLE_RETRIES:]) and passes[-1] == ["AAPL"]       # RUTW ya no se pide


async def test_the_ideas_quotes_survive_a_short_wait_and_are_stored_in_phases(tmp_path):
    """Antes la pestaña Ideas cancelaba la cotización a los 30 s: no se guardaba nada y cada visita volvía a pedir las cadenas."""
    import asyncio
    svc, provider = make(tmp_path)
    gate = asyncio.Event()

    async def slow_closes(tickers, days, timeout=20.0):
        provider.calls.append("closes")
        await gate.wait()
        return {"MPC": [(date(2026, 10, 15), 470.0)]}

    provider.get_daily_closes = slow_closes
    task = svc.community_quotes_task()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), 0.3)                    # la página deja de esperar...
    assert not task.done() and svc.community_quotes_running()               # ...pero la cotización sigue
    live = svc._community_live
    assert live is not None and live[2] and live[3]["MPC"].underlying_price == 476.0 and live[4] == {}      # patas y precios ya guardados
    assert svc.community_quotes_task() is task                              # otra visita no lanza otra
    gate.set()
    await svc.wait_idle()
    assert svc._community_live[4] == {"MPC": [(date(2026, 10, 15), 470.0)]} and provider.calls.count("chain") == 1


def test_ideas_page_shows_a_notice_while_the_live_quotes_are_still_running(client_and_service, tmp_path, monkeypatch):
    import asyncio
    from scanner_opciones.ui import web
    client, svc, *_ = client_and_service
    path = tmp_path / "tortuga.json"
    path.write_text(ideas_payload(), encoding="utf-8")
    object.__setattr__(svc.settings, "community", svc.settings.community.model_copy(update={"path": str(path)}))
    provider = CountingProvider()
    gate = asyncio.Event()

    async def slow_closes(tickers, days, timeout=20.0):
        await gate.wait()
        return {}

    provider.get_daily_closes = slow_closes
    svc.volatility = provider
    monkeypatch.setattr(web, "IDEAS_WAIT_SECONDS", 0.2)
    page = client.get("/comunidad").text
    assert 'id="quoting"' in page and "se recarga sola" in page
    client.portal.call(gate.set)
    client.portal.call(svc.wait_idle)
    assert 'id="quoting"' not in client.get("/comunidad").text


async def test_the_quotes_of_the_ideas_survive_a_restart_when_the_market_is_closed(tmp_path):
    first, provider = make(tmp_path)
    await first.refresh_community_quotes()
    assert provider.calls
    settings = Settings.model_validate({"community": {"path": str(tmp_path / "tortuga.json")}})
    gw = FakeGateway()
    gw.connected = True
    again = CountingProvider()
    second = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW, volatility=again)   # reinicio: misma base de datos
    await second.refresh_community_quotes()
    assert again.calls == []                                                # reutiliza la cotización guardada tras el cierre
    assert second._community_live is not None and second._community_live[2]   # con las patas cotizadas
