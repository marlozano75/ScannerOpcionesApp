"""Reintento automático de la conexión con el broker: no repite la parte de tastytrade."""
from scanner_opciones.domain.errors import BrokerDisconnectedError
from tests.integration.test_service import make_service, seed_market


class FlakyConnect:
    """Envuelve el gateway: las primeras `fails` conexiones fallan."""

    def __init__(self, gw, fails):
        self._gw, self.fails = gw, fails

    def __getattr__(self, name):
        return getattr(self._gw, name)

    async def connect(self):
        if self.fails > 0:
            self.fails -= 1
            raise BrokerDisconnectedError("TWS no responde")
        await self._gw.connect()


async def test_reconnect_recovers_and_does_not_repeat_tastytrade_update():
    svc, gw = make_service()
    seed_market(gw)
    svc.gateway = FlakyConnect(gw, fails=1)
    runs = []

    async def fake_history():
        runs.append(1)

    svc._update_history_and_fundamentals = fake_history

    await svc.start()
    assert not svc.state.connected and "connection" in svc.state.errors
    assert runs == [1]

    assert await svc.reconnect() is True
    assert svc.state.connected and "connection" not in svc.state.errors
    assert runs == [1]          # no se vuelve a descargar lo de tastytrade


async def test_reconnect_does_nothing_when_connected():
    svc, gw = make_service()
    await svc.start()
    assert svc.state.connected
    assert await svc.reconnect() is False


async def test_no_refresh_while_known_disconnected():
    svc, gw = make_service()
    svc.gateway = FlakyConnect(gw, fails=1)
    await svc.start()
    calls = []
    svc.refresh_job.run = lambda *a, **k: calls.append(1)
    assert await svc.refresh_all() is False
    assert await svc.refresh_all(include_market=False) is False
    assert await svc.refresh_account() is False
    assert calls == []
