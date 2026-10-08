"""Cuenta/VIX en su propio bloqueo y cotizaciones por lotes encadenados."""
import asyncio

from tests.integration.test_jobs import Env, TODAY, NOW, _prepare_refresh
from tests.integration.test_service import make_service, seed_market, started_service

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import OptionQuote


async def test_account_refresh_is_not_blocked_by_a_long_market_cycle():
    svc, gw = await started_service()
    svc.state.account = None
    async with svc._lock:                          # un ciclo de mercado largo ocupa el bloqueo de los jobs
        assert svc.busy
        assert await svc.refresh_account() is True
    assert svc.state.account is not None and svc.state.risk is not None and svc.state.vix is not None
    assert svc.state.last_account_refresh == NOW


async def test_account_refresh_does_not_stack():
    svc, gw = await started_service()
    gate = asyncio.Event()
    original = gw.get_account_summary

    async def slow():
        await gate.wait()
        return await original()
    gw.get_account_summary = slow
    first = asyncio.create_task(svc.refresh_account())
    await asyncio.sleep(0)
    assert await svc.refresh_account() is False    # ya hay uno en curso: se omite
    gate.set()
    assert await first is True


async def test_market_refresh_updates_the_account_while_quotes_are_still_pending():
    svc, gw = await started_service()
    svc.state.account = None
    gate = asyncio.Event()
    original = gw.get_quotes

    async def slow(contracts):
        await gate.wait()
        return await original(contracts)
    gw.get_quotes = slow
    running = asyncio.create_task(svc.refresh_all())
    for _ in range(20):
        await asyncio.sleep(0)
    assert svc.state.account is not None           # la cuenta no esperó a las cotizaciones
    assert not running.done()
    gate.set()
    assert await running is True


async def test_account_only_refresh_marks_the_cycle():
    svc, gw = await started_service()
    before = svc.state.data_version
    assert await svc.refresh_all(include_market=False) is True
    assert svc.state.data_version == before + 1 and svc.state.last_refresh == NOW


async def test_account_connection_loss_marks_disconnected():
    svc, gw = await started_service()
    gw.connected = False
    assert await svc.refresh_account() is False
    assert not svc.state.connected


async def test_refresh_uses_the_gateway_batch_size_and_keeps_requesting_while_processing():
    env = Env()
    c75, c80 = await _prepare_refresh(env)
    env.gw.quote_batch_size = 1                    # como el HybridGateway: manda el lote del gateway, no refresh.batch_size
    sizes, order = [], []
    original = env.gw.get_quotes

    async def spy(contracts):
        sizes.append(len(contracts))
        order.append(("get", tuple(c.strike for c in contracts)))
        return await original(contracts)
    env.gw.get_quotes = spy
    margin = env.gw.what_if_margin

    async def margin_spy(contract, quantity=1):
        await asyncio.sleep(0)                     # IBKR responde por red: cede el control, como el real
        order.append(("margin", contract.strike))
        return await margin(contract, quantity)
    env.gw.what_if_margin = margin_spy
    report = await env.refresh.run()
    assert sizes == [1, 1] and report.refreshed == 2
    # el segundo lote ya se había pedido cuando se procesó el primero
    assert order.index(("get", (80.0,))) < order.index(("margin", 75.0))


async def test_refresh_stops_on_disconnection_and_cancels_the_prefetch():
    env = Env()
    await _prepare_refresh(env)
    env.gw.quote_batch_size = 1
    env.gw.connected = False
    import pytest
    from scanner_opciones.domain.errors import BrokerDisconnectedError
    with pytest.raises(BrokerDisconnectedError):
        await env.refresh.run()


async def test_margins_are_not_requested_after_consecutive_what_if_failures():
    """Si TWS no responde, no se espera el tiempo máximo por cada contrato que pasa el escaneo."""
    from scanner_opciones.domain.errors import DataUnavailableError
    from scanner_opciones.jobs import refresh as refresh_module

    env = Env()
    await _prepare_refresh(env)
    asked = []

    async def silent(contract, quantity=1):
        asked.append(contract)
        raise DataUnavailableError("TWS no respondió")
    env.gw.what_if_margin = silent
    env.settings = Settings.model_validate({"scanner": {"initial": {"min_annual_yield_pct": 0}}})
    env.refresh.settings = env.settings
    refresh_module.MARGIN_MAX_FAILURES, saved = 1, refresh_module.MARGIN_MAX_FAILURES
    try:
        report = await env.refresh.run()
    finally:
        refresh_module.MARGIN_MAX_FAILURES = saved
    assert len(asked) == 1 and report.margins_requested == 1     # tras el primer fallo no se piden más
    assert report.refreshed == 2                                   # los snapshots se guardan igualmente, sin margen


async def test_account_timeout_is_reported_instead_of_hanging(monkeypatch):
    from scanner_opciones.app import service as service_module

    svc, gw = await started_service()

    async def silent():
        await asyncio.Event().wait()
    gw.get_account_summary = silent
    monkeypatch.setattr(service_module, "STEP_TIMEOUT_SECONDS", 0.05)
    assert await svc.refresh_account() is True
    assert "portfolio" in svc.state.errors and "Timeout" in svc.state.errors["portfolio"]


def test_sdk_log_level_survives_later_imports():
    import logging
    from scanner_opciones.marketdata.tastytrade import quiet_sdk_logging

    quiet_sdk_logging(logging.WARNING)
    import importlib
    import tastytrade.streamer   # noqa: F401 - un import posterior no debe volver a ponerlo en DEBUG
    importlib.import_module("tastytrade.instruments")
    assert logging.getLogger("tastytrade").level == logging.WARNING


async def test_a_stale_margin_is_kept_when_tws_does_not_answer():
    """Con TWS caído, el margen antiguo (más viejo que margin_max_age_minutes) no se borra."""
    from datetime import timedelta
    from scanner_opciones.domain.errors import DataUnavailableError

    env = Env()
    c75, c80 = await _prepare_refresh(env)
    env.settings = Settings.model_validate({"scanner": {"initial": {"min_annual_yield_pct": 0}}})
    env.refresh.settings = env.settings
    await env.refresh.run()                                    # guarda el margen de 1500
    assert env.snaps.all("AAPL")[0].initial_margin == 1500.0
    env.clock = NOW + timedelta(hours=3)                       # el margen ya es antiguo y se querría pedir de nuevo

    async def silent(contract, quantity=1):
        raise DataUnavailableError("TWS no respondió")
    env.gw.what_if_margin = silent
    await env.refresh.run()
    kept = [s for s in env.snaps.all("AAPL") if s.contract == c75][0]
    assert kept.initial_margin == 1500.0 and kept.margin_at == NOW      # sigue ahí, con su fecha antigua
