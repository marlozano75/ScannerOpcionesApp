"""IBKRGateway con un IB falso: esperas que terminan antes, dividendos en lote y sin recualificar."""
import asyncio
import time
from datetime import date, datetime
from types import SimpleNamespace as NS

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import IbkrSettings
from scanner_opciones.domain.models import OptionContract

TODAY = date(2026, 9, 29)


def _tick(**kw):
    base = dict(bid=1.0, ask=1.2, last=1.1, bidSize=10, modelGreeks=NS(delta=-0.1, impliedVol=0.3),
                putOpenInterest=100, callOpenInterest=None, dividends=None, marketPrice=lambda: 10.0)
    base.update(kw)
    return NS(**base)


class FakeIB:
    def __init__(self, ticks=None):
        self.ticks = ticks or {}
        self.qualified = 0
        self.whatif = 0

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, *c):
        self.qualified += len(c)
        for i, x in enumerate(c):
            x.conId = x.conId or 1000 + i
        return list(c)

    def reqMktData(self, contract, generic, *a):
        key = getattr(contract, "strike", None) or contract.symbol
        return self.ticks[key]

    def cancelMktData(self, contract):
        pass

    async def whatIfOrderAsync(self, opt, order):
        self.whatif += 1
        return NS(initMarginChange="1500")


def make(fake, wait=4):
    gw = IBKRGateway(IbkrSettings(quote_wait_seconds=wait), now=lambda: datetime(2026, 9, 29, 10))
    gw.ib = fake
    return gw


def put(strike, con_id=None):
    return OptionContract("AAPL", date(2026, 10, 30), strike, con_id=con_id)


async def test_get_quotes_returns_as_soon_as_everything_arrived():
    gw = make(FakeIB({75.0: _tick(), 80.0: _tick()}), wait=4)
    t0 = time.monotonic()
    out = await gw.get_quotes([put(75.0, 1), put(80.0, 2)])
    assert time.monotonic() - t0 < 1.0                      # antes dormía siempre 4 s
    assert out[put(75.0, 1)].bid == 1.0 and out[put(75.0, 1)].open_interest == 100


async def test_get_quotes_waits_the_maximum_if_data_never_completes():
    gw = make(FakeIB({75.0: _tick(bid=float("nan"))}), wait=0.5)
    t0 = time.monotonic()
    out = await gw.get_quotes([put(75.0, 1)])
    assert 0.4 < time.monotonic() - t0 < 1.5                # se queda con lo que haya
    assert out[put(75.0, 1)].bid is None and out[put(75.0, 1)].ask == 1.2


async def test_dividends_are_requested_in_one_wait():
    div = NS(nextDate=date(2026, 10, 9))
    fake = FakeIB({"AAPL": _tick(dividends=div), "KO": _tick(dividends=div)})
    gw = make(fake)
    gw._stocks = {"AAPL": NS(symbol="AAPL"), "KO": NS(symbol="KO")}
    t0 = time.monotonic()
    out = await gw.get_days_to_ex_dividend_many(["AAPL", "KO"])
    assert out == {"AAPL": 10, "KO": 10} and time.monotonic() - t0 < 1.0


async def test_what_if_does_not_requalify_known_contracts():
    fake = FakeIB()
    gw = make(fake)
    assert await gw.what_if_margin(put(75.0, con_id=123)) == 1500.0
    assert fake.qualified == 0 and fake.whatif == 1
    await gw.what_if_margin(put(75.0))                      # sin conId sí se cualifica
    assert fake.qualified == 1


async def test_concurrent_quiet_contexts_keep_the_filter_until_the_last_exits():
    import logging

    from scanner_opciones.broker import ibkr_gateway as g

    lg = logging.getLogger("ib_async.wrapper")
    with g._quiet_unknown_contracts():
        with g._quiet_unknown_contracts():
            assert g._UNKNOWN_FILTER in lg.filters
        assert g._UNKNOWN_FILTER in lg.filters              # el primero sigue dentro
    assert g._UNKNOWN_FILTER not in lg.filters
