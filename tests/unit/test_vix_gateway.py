"""VIX vía IBKRGateway con un IB falso: debe usar solo históricos y no colgarse sin suscripción."""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from eventkit import Event

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


class QuoteIB:
    """IB falso para cotizaciones y precios: reqMktData devuelve tickers con los campos de ib_async."""

    def __init__(self):
        self.cancelled = []
        self.errorEvent = Event()
        self.prices = {"AAPL": 210.5, "KO": float("nan"), "MU": 90.0}
        self.ivs = {"AAPL": 0.31, "KO": float("nan"), "MU": float("nan")}

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, *contracts):
        for c in contracts:
            c.conId = 42
        return list(contracts)

    def reqMktData(self, contract, generic, snapshot, regulatory):
        symbol = contract.symbol
        if contract.secType == "STK":
            px = self.prices.get(symbol, float("nan"))
            iv = self.ivs.get(symbol, float("nan"))
            return NS(marketPrice=lambda px=px: px, impliedVolatility=iv)
        return NS(
            bid=1.0, ask=1.2, last=1.1, bidSize=37.0, putOpenInterest=500.0, callOpenInterest=float("nan"),
            modelGreeks=NS(delta=-0.12, impliedVol=0.31),
        )

    def cancelMktData(self, contract):
        self.cancelled.append(contract.symbol)


async def test_get_quotes_includes_bid_size():
    from scanner_opciones.domain.models import OptionContract

    gw = IBKRGateway(IbkrSettings(quote_wait_seconds=0.05))
    gw.ib = QuoteIB()
    c = OptionContract("AAPL", date(2026, 10, 30), 150.0)
    q = (await gw.get_quotes([c]))[c]
    assert q.bid == 1.0 and q.ask == 1.2 and q.bid_size == 37 and q.open_interest == 500


async def test_get_underlying_quotes_returns_price_and_live_iv():
    gw = IBKRGateway(IbkrSettings(quote_wait_seconds=0.3))
    gw.ib = QuoteIB()
    quotes = await gw.get_underlying_quotes(["AAPL", "KO", "MU"])
    assert quotes["AAPL"].price == 210.5 and quotes["AAPL"].iv == 0.31
    assert "KO" not in quotes                                # sin precio ni IV: no aparece
    assert quotes["MU"].price == 90.0 and quotes["MU"].iv is None   # precio sí, IV no
    assert sorted(gw.ib.cancelled) == ["AAPL", "KO", "MU"]   # las líneas de mercado se liberan


async def test_get_iv_history_returns_close_high_low():
    class HistIB(QuoteIB):
        async def reqHistoricalDataAsync(self, contract, **kw):
            self.what = kw["whatToShow"]
            return [
                NS(date=date(2026, 9, 28), close=0.30, high=0.33, low=0.28),
                NS(date=date(2026, 9, 29), close=0.31, high=float("nan"), low=-1),   # sin máx/mín válidos
            ]

    gw = IBKRGateway(IbkrSettings(), now=lambda: datetime(2026, 9, 29, 10))
    gw.ib = HistIB()
    out = await gw.get_iv_history("AAPL", None)
    assert gw.ib.what == "OPTION_IMPLIED_VOLATILITY"
    assert out == [(date(2026, 9, 28), 0.30, 0.33, 0.28), (date(2026, 9, 29), 0.31, None, None)]
    since = await gw.get_iv_history("AAPL", date(2026, 9, 29))
    assert [p[0] for p in since] == [date(2026, 9, 29)]                  # incluye el día indicado


class NoisyIB(QuoteIB):
    """qualifyContractsAsync que imita a ib_async: por cada contrato inexistente registra Error 200 + Unknown contract."""

    EXIST = {150.0, 155.0}

    async def qualifyContractsAsync(self, *contracts):
        import logging as _l
        for c in contracts:
            if getattr(c, "strike", None) in self.EXIST or c.secType == "STK":
                c.conId = 7
            else:
                _l.getLogger("ib_async.wrapper").error(
                    "Error 200, reqId 9: No se encuentra definición del activo solicitado, contract: %s", c)
                _l.getLogger("ib_async.ib").warning("Unknown contract: %s", c)
        return list(contracts)


async def test_qualify_contracts_hides_expected_unknown_contract_noise(caplog):
    import logging as _l
    from scanner_opciones.domain.models import OptionContract

    gw = IBKRGateway(IbkrSettings())
    gw.ib = NoisyIB()
    contracts = [OptionContract("BAC", date(2026, 10, 2), k) for k in (32.0, 150.0, 155.0, 33.0)]
    with caplog.at_level(_l.DEBUG):
        valid = await gw.qualify_contracts(contracts)
    assert [c.strike for c in valid] == [150.0, 155.0] and all(c.con_id == 7 for c in valid)
    assert not [r for r in caplog.records if "Error 200" in r.getMessage() or "Unknown contract" in r.getMessage()]


async def test_other_errors_still_logged_and_filter_is_removed_afterwards(caplog):
    import logging as _l
    from scanner_opciones.domain.models import OptionContract

    class MixedIB(NoisyIB):
        async def qualifyContractsAsync(self, *contracts):
            _l.getLogger("ib_async.wrapper").error("Error 354, reqId 3: No está suscrito a los datos de mercado")
            return await super().qualifyContractsAsync(*contracts)

    gw = IBKRGateway(IbkrSettings())
    gw.ib = MixedIB()
    with caplog.at_level(_l.DEBUG):
        await gw.qualify_contracts([OptionContract("BAC", date(2026, 10, 2), 32.0)])
        assert any("Error 354" in r.getMessage() for r in caplog.records)      # los demás errores se conservan
        caplog.clear()
        # fuera de la validación el filtro ya no está: un Error 200 real se vería
        _l.getLogger("ib_async.wrapper").error("Error 200, reqId 1: fuera de la validación")
        assert any("Error 200" in r.getMessage() for r in caplog.records)
