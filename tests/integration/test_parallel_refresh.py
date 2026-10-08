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
