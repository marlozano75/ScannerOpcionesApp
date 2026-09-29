"""VIX vía IBKRGateway con un IB falso: debe usar solo históricos y no colgarse sin suscripción."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import IbkrSettings
from scanner_opciones.domain.errors import DataUnavailableError

TODAY = date(2026, 9, 29)


class FakeIB:
    def __init__(self, hang_futures=False, no_futures=False):
        self.hang_futures = hang_futures
        self.no_futures = no_futures
        self.calls = []

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, *c):
        return list(c)

    async def reqHistoricalDataAsync(self, contract, **kw):
        self.calls.append((getattr(contract, "symbol", None), getattr(contract, "lastTradeDateOrContractMonth", None)))
        if contract.secType == "FUT":
            base = 19.0 if contract.lastTradeDateOrContractMonth.endswith("14") else 20.0
            return [NS(date=TODAY - timedelta(days=1), close=base - 0.5), NS(date=TODAY, close=base)]
        return [NS(date=TODAY - timedelta(days=i), close=15.0 + i) for i in range(8, -1, -1)]

    async def reqContractDetailsAsync(self, contract):
        if self.hang_futures:
            await asyncio.sleep(3600)
        if self.no_futures:
            return []
        exps = ["20261014", "20261021", "20261118", "20260901", "202612"]  # uno pasado, uno 'YYYYMM'
        out = []
        for e in exps:
            c = type(contract)(contract.symbol, exchange="CFE", currency="USD",
                               lastTradeDateOrContractMonth=e)
            out.append(NS(contract=c))
        return out

    def reqMktData(self, *a, **k):  # no debe usarse para el VIX
        raise AssertionError("El VIX no debe pedir market data en streaming")


def make(fake):
    gw = IBKRGateway(IbkrSettings(), now=lambda: datetime(2026, 9, 29, 10))
    gw.ib = fake
    return gw


async def test_vix_uses_historical_bars_only():
    gw = make(FakeIB())
    v = await gw.get_vix_data(5, 2)
    assert v.current == 15.0 + 0            # último cierre histórico
    assert len(v.last_closes) == 5 and v.last_closes[-1][0] == TODAY
    # futuros: sin el vencimiento pasado ni el 'YYYYMM'; ordenados; solo 2
    assert [d for d, _ in v.futures] == [date(2026, 10, 14), date(2026, 10, 21)]
    assert v.futures[0][1] == 19.0


async def test_vix_survives_futures_timeout(monkeypatch):
    gw = make(FakeIB(hang_futures=True))
    real = asyncio.wait_for

    async def fast(coro, timeout):
        return await real(coro, timeout=0.05)
    monkeypatch.setattr("scanner_opciones.broker.ibkr_gateway.asyncio.wait_for", fast)
    v = await gw.get_vix_data(5, 3)
    assert v.current is not None and v.futures == []


async def test_vix_without_futures_data():
    v = await make(FakeIB(no_futures=True)).get_vix_data(5, 3)
    assert v.current == 15.0 and v.futures == []


async def test_historical_timeout_becomes_data_unavailable(monkeypatch):
    class Hang(FakeIB):
        async def reqHistoricalDataAsync(self, *a, **k):
            await asyncio.sleep(3600)

    gw = make(Hang())
    gw.HISTORICAL_TIMEOUT = 0.05
    with pytest.raises(DataUnavailableError):
        await gw.get_vix_data(5, 3)
