"""Modos por ticker (`scanner.ticker_modes`): qué se cotiza y qué se escanea según precio, IV y liquidez."""
from datetime import timedelta

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings, TickerModesSettings
from scanner_opciones.domain.models import OptionChain, OptionQuote, TickerInfo
from scanner_opciones.app.service import AppService
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, TODAY, FixedMarket


def make(enabled=True):
    settings = Settings()
    settings = settings.model_copy(update={"scanner": settings.scanner.model_copy(update={"ticker_modes": TickerModesSettings(enabled=enabled)})})
    gw = FakeGateway()
    svc = AppService(gw, Database(":memory:"), settings, lambda: NOW, market=FixedMarket(True))
    for t, price in (("LOW", 100.0), ("BIG", 500.0), ("ILLQ", 100.0)):
        gw.sectors[t] = ("Technology", "Software")
        gw.prices[t] = price
        gw.chains[t] = OptionChain(t, [TODAY + timedelta(days=30)], [price * 0.75, price * 0.8, price * 0.9])
    svc.watchlist.add(["LOW", "BIG", "ILLQ"], NOW)
    return svc, gw


async def _prepare(svc, gw, liquidity):
    await svc.start()
    await svc.wait_idle()
    svc.quality.save([TickerInfo(t, option_liquidity=l, eps_ttm=5.0, positive_quarters=4, reported_quarters=4) for t, l in liquidity.items()])
    for c in svc.contracts.list():
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500, delta=-0.2, iv=0.4)
        gw.margins[c] = 1_500.0


async def test_modes_decide_what_is_quoted_and_scanned():
    svc, gw = make()
    await _prepare(svc, gw, {"LOW": 3, "BIG": 3, "ILLQ": 2})
    await svc.refresh_all()
    modes = svc.ticker_modes()
    # LOW: precio bajo y spreads (liquidez 3, IV de la ficha del test); BIG: precio alto, solo spreads; ILLQ: liquidez 2 (mín. 2 en puts, 3 en spreads) -> solo puts desnudas
    assert modes["LOW"].put and not modes["BIG"].put and modes["ILLQ"].put and not modes["ILLQ"].put_spread
    quoted = {s.contract.ticker for s in svc.snapshots.all()}
    assert "LOW" in quoted and "ILLQ" in quoted               # puts cotizadas: sirven a puts desnudas
    assert svc.naked_blocked().get("BIG", "").startswith("precio")
    assert "ILLQ" not in svc.naked_blocked()


async def test_ticker_with_no_mode_is_not_quoted_and_history_is_recorded():
    svc, gw = make()
    await _prepare(svc, gw, {"LOW": 3, "BIG": 1, "ILLQ": 3})   # BIG: caro y sin liquidez -> ningún modo
    await svc.refresh_all()
    assert not svc.ticker_modes()["BIG"].any
    assert all(s.contract.ticker != "BIG" for s in svc.snapshots.all())
    rows = svc.meta.db.conn.execute("SELECT ticker, put, put_spread, cds FROM ticker_mode_history WHERE day = ?", (TODAY.isoformat(),)).fetchall()
    assert {r["ticker"] for r in rows} == {"LOW", "BIG", "ILLQ"}
    # sin cambios, una segunda evaluación no reescribe nada
    before = svc.meta.db.data_changes
    svc.resolve_modes(svc.ticker_info.all())
    assert svc.meta.db.data_changes == before


async def test_disabled_modes_change_nothing():
    svc, gw = make(enabled=False)
    await _prepare(svc, gw, {"LOW": 3, "BIG": 1, "ILLQ": 1})
    await svc.refresh_all()
    assert svc.ticker_modes() == {} and {s.contract.ticker for s in svc.snapshots.all()} == {"LOW", "BIG", "ILLQ"}


async def test_held_tickers_keep_every_mode():
    svc, gw = make()
    await _prepare(svc, gw, {"LOW": 3, "BIG": 1, "ILLQ": 3})
    svc.held_tickers = lambda: {"BIG": {}}       # tiene una posición abierta: el Panel necesita sus cotizaciones
    await svc.refresh_all()
    assert svc.ticker_modes()["BIG"].any and any(s.contract.ticker == "BIG" for s in svc.snapshots.all())
