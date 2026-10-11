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


# ---- sin TWS pero con tastytrade: se actualiza todo lo que no es de TWS -------------------------------------------

from datetime import timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.config.settings import MarketDataSettings, Settings
from scanner_opciones.domain.models import AccountSummary, OptionQuote, VixData
from scanner_opciones.marketdata.options import FakeOptionData, listing_key
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, TODAY, FixedMarket


class DownFake(FakeGateway):
    """TWS cerrado hasta que `up` sea True."""
    up = False

    async def connect(self):
        if not self.up:
            raise BrokerDisconnectedError("TWS no responde")
        await super().connect()


def offline_service(db=None):
    expiry = TODAY + timedelta(days=30)
    lst = {listing_key(expiry, k): f".AAPL{k:g}" for k in (75.0, 80.0)}
    provider = FakeOptionData(
        listings={"AAPL": lst}, prices={"AAPL": 100.0},
        quotes={s: OptionQuote(bid=2.0, ask=2.2, open_interest=500) for s in lst.values()},
        vix=VixData(18.0, [(TODAY - timedelta(days=1), 17.0)], []),
    )
    inner = DownFake(account=AccountSummary("DU1", net_liquidation=100_000, excess_liquidity=45_000, cushion_pct=45.0))
    inner.sectors["AAPL"] = ("Technology", "Electronics")
    gw = HybridGateway(inner, provider, MarketDataSettings(), now=lambda: NOW)
    svc = AppService(gw, db or Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True))
    svc.watchlist.add(["AAPL"], NOW)
    return svc, inner, provider


async def test_without_tws_everything_from_tastytrade_is_updated():
    svc, inner, provider = offline_service()
    await svc.start()
    await svc.wait_idle()
    assert not svc.state.connected and "connection" in svc.state.errors
    info = svc.ticker_info.get("AAPL")
    assert info.underlying_price == 100.0 and info.sector is None       # el sector es de TWS: queda pendiente
    assert svc.watchlist.pending_daily_update(TODAY) == []              # la actualización diaria se hizo
    snaps = svc.snapshots.all()
    assert snaps and all(s.bid == 2.0 and s.initial_margin is None for s in snaps)   # cotizado, sin márgenes
    assert svc.state.vix is not None and svc.state.account is None


async def test_reconnect_only_completes_what_tws_provides():
    svc, inner, provider = offline_service()
    await svc.start()
    await svc.wait_idle()
    for c in svc.contracts.list():
        inner.margins[c] = 1_500.0
    calls_before = [c for c in provider.calls if c[0] != "get_vix"]
    inner.up = True
    assert await svc.reconnect() is True
    assert svc.state.connected and "connection" not in svc.state.errors
    assert svc.ticker_info.get("AAPL").sector == "Technology"
    assert svc.state.account is not None and svc.state.risk is not None
    assert any(s.initial_margin == 1_500.0 for s in svc.snapshots.all())
    assert [c for c in provider.calls if c[0] != "get_vix"] == calls_before   # no se repite nada de tastytrade (salvo el VIX, que es de cada minuto)


async def test_last_account_is_shown_after_a_restart_without_tws():
    db = Database(":memory:")
    svc, inner, provider = offline_service(db)
    inner.up = True
    await svc.start()
    await svc.wait_idle()
    assert svc.state.account is not None
    again, inner2, _ = offline_service(db)               # reinicio con TWS cerrado: misma base de datos
    assert again.state.account is not None and again.state.account.net_liquidation == 100_000
    assert again.state.risk is not None and not again.state.connected


async def test_daily_update_with_nothing_pending_does_not_repeat_history_and_quality():
    svc, inner, provider = offline_service()
    await svc.start()
    await svc.wait_idle()
    runs = []

    async def count():
        runs.append(1)
    svc._update_history, svc._update_fundamentals = count, count
    await svc.run_daily()                      # todo está actualizado hoy: nada pendiente
    assert runs == []
    await svc.run_daily(["AAPL"], wait=True)   # una actualización real sí los repite
    assert runs == [1, 1]


async def test_vix_is_not_refreshed_while_the_market_is_closed_and_the_close_is_captured():
    svc, inner, provider = offline_service()
    await svc.start()
    await svc.wait_idle()
    svc.market.open_ = False
    svc.market.last_close_dt = svc.state.vix.updated_at.astimezone() - timedelta(hours=1)   # el cierre es anterior
    calls = lambda: len([c for c in provider.calls if c[0] == "get_vix"])
    before = calls()
    await svc.refresh_account(skip_closed_vix=True)
    assert calls() == before                   # cerrado y ya capturado: no se pide
    await svc.refresh_account()                # manual: siempre completo
    assert calls() == before + 1
    svc.market.last_close_dt = svc.state.vix.updated_at.astimezone() + timedelta(hours=1)   # el cierre es posterior
    await svc.refresh_account(skip_closed_vix=True)
    assert calls() == before + 2               # falta la captura del cierre


async def test_periodic_refresh_downloads_quality_only_after_a_daily_recalculation():
    from scanner_opciones.marketdata.fundamentals import FakeFundamentals, Fundamentals
    svc, inner, provider = offline_service()
    fake = FakeFundamentals({"AAPL": Fundamentals(8.7, 4.8e12, 4, TODAY + timedelta(days=30))}, {"AAPL": [1.0] * 4})
    svc.daily.fundamentals = fake
    await svc.start()
    await svc.wait_idle()
    calls = lambda: len([c for c in fake.calls if c[0] == "get_fundamentals"])
    before = calls()
    await svc.refresh_periodic()
    await svc.wait_idle()
    assert calls() == before                       # ya está al día: no pide nada
    svc.now = lambda: NOW + timedelta(days=1)      # pasa otro recálculo diario
    svc.daily.now = svc.now
    await svc.refresh_periodic()
    await svc.wait_idle()
    assert calls() == before + 1
