"""Instrumentos que no son acciones de EE. UU. (Euro Stoxx 50 en Eurex, euros): contratos, tipo de datos, enrutado y velas de Yahoo."""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest
from eventkit import Event

from scanner_opciones.broker import ibkr_mapper as m
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import IbkrSettings, MarketDataSettings, Settings, default_instruments
from scanner_opciones.domain.errors import DataUnavailableError
from scanner_opciones.domain.models import OptionChain, OptionContract
from scanner_opciones.marketdata.candles import FakeCandles, SplitCandles
from scanner_opciones.marketdata.options import FakeOptionData
from scanner_opciones.marketdata.yahoo import ohlc_from_frame

INSTR = default_instruments()
E1 = date(2026, 11, 20)


def estx(strike=5450.0, con_id=None):
    return OptionContract("ESTX50", E1, strike, multiplier=10, con_id=con_id)


def test_estx50_is_configured_by_default_and_exempt_from_company_filters():
    s = Settings()
    i = s.instruments["ESTX50"]
    assert (i.symbol, i.exchange, i.currency, i.multiplier, i.trading_class, i.yahoo_symbol) == ("ESTX50", "EUREX", "EUR", 10, "OESX", "^STOXX50E")
    assert "=ESTX50" in s.scanner.quality.exempt_entries


def test_pick_chain_prefers_the_exchange_class_and_multiplier():
    chains = [NS(exchange="EUREX", tradingClass="OEXP", multiplier="10", expirations=list("abcdefgh")),
              NS(exchange="EUREX", tradingClass="OESX", multiplier="10", expirations=list("ab")),
              NS(exchange="SMART", tradingClass="ESTX50", multiplier="100", expirations=list("abc"))]
    assert m.pick_chain(chains, "ESTX50", "EUREX", "OESX", 10).tradingClass == "OESX"
    assert m.pick_chain([NS(exchange="SMART", tradingClass="AAPL", multiplier="100", expirations=["a"]),
                         NS(exchange="SMART", tradingClass="AAPL", multiplier="10", expirations=list("abc"))], "AAPL").multiplier == "100"


class FakeIB:
    """TWS falso. `frozen`: conexión que pidió datos congelados (último bid/ask, sin open interest); si no, diferidos (OI y, con el mercado cerrado, sin bid/ask)."""

    def __init__(self, frozen=False, closed=False):
        self.errorEvent = Event()
        self.frozen = frozen
        self.closed = closed
        self.types: list[int] = []
        self.requested: list = []

    def isConnected(self):
        return True

    def managedAccounts(self):
        return ["U1"]

    def reqMarketDataType(self, t):
        self.types.append(t)

    async def qualifyContractsAsync(self, *c):
        for i, x in enumerate(c):
            x.conId = x.conId or 7000 + i
        return list(c)

    async def reqSecDefOptParamsAsync(self, symbol, exch, sec, con_id):
        return [NS(exchange="EUREX", tradingClass="OEXP", multiplier="10", expirations=["20261026"], strikes=[6000.0]),
                NS(exchange="EUREX", tradingClass="OESX", multiplier="10", expirations=["20261120", "20261106"], strikes=[5400.0, 5450.0])]

    def reqMktData(self, contract, generic, *a):
        self.requested.append((contract, generic))
        nan = float("nan")
        quoted = self.frozen or not self.closed
        return NS(bid=10.0 if quoted else -1, ask=11.0 if quoted else -1, last=None, bidSize=5 if quoted else nan,
                  modelGreeks=NS(delta=-0.09, impliedVol=0.29), putOpenInterest=nan if self.frozen else 900, callOpenInterest=None,
                  marketPrice=lambda: 6068.22, impliedVolatility=nan, close=6087.0)

    def cancelMktData(self, contract):
        pass


def gateway(closed=False):
    gw = IBKRGateway(IbkrSettings(market_data_type=2, quote_wait_seconds=0.3), now=lambda: datetime(2026, 10, 25, 12), instruments=INSTR)
    gw.ib = FakeIB(frozen=True)              # la principal pide datos congelados
    gw._ib_aux = FakeIB(closed=closed)       # la de los instrumentos, diferidos
    return gw


def test_option_contracts_carry_exchange_currency_and_class():
    gw = gateway()
    opt = gw._ib_option(estx())
    assert (opt.symbol, opt.exchange, opt.currency, opt.tradingClass, opt.multiplier, opt.right) == ("ESTX50", "EUREX", "EUR", "OESX", "10", "P")
    us = gw._ib_option(OptionContract("AAPL", E1, 200.0))
    assert (us.exchange, us.currency, us.multiplier) == ("SMART", "USD", "100")


async def test_chain_comes_from_the_instrument_class():
    gw = gateway()
    chain = await gw.get_option_chain("ESTX50")
    assert chain == OptionChain("ESTX50", [date(2026, 11, 6), E1], [5400.0, 5450.0], 10)
    assert await gw.get_sector_info("ESTX50") == ("Índice Europa", None)
    assert await gw.get_days_to_ex_dividend("ESTX50") is None
    assert await gw.get_days_to_ex_dividend_many(["ESTX50"]) == {"ESTX50": None}


async def test_instruments_are_quoted_on_their_own_connection_and_stocks_on_the_main_one():
    gw = gateway()
    out = await gw.get_quotes([estx(5450.0, 1)])
    q = out[estx(5450.0, 1)]
    assert (q.bid, q.ask, q.delta, q.open_interest) == (10.0, 11.0, -0.09, 900)      # el open interest solo llega por la de datos diferidos
    assert len(gw._ib_aux.requested) == 1 and gw.ib.requested == []
    await gw.get_quotes([estx(5450.0, 1), OptionContract("AAPL", E1, 200.0, con_id=2)])
    assert len(gw.ib.requested) == 1 and gw.ib.requested[0][0].symbol == "AAPL"


async def test_closed_market_completes_the_missing_bid_and_ask_with_the_main_connection():
    gw = gateway(closed=True)
    q = (await gw.get_quotes([estx(5450.0, 1)]))[estx(5450.0, 1)]
    assert (q.bid, q.ask, q.bid_size, q.open_interest, q.delta) == (10.0, 11.0, 5, 900, -0.09)       # bid/ask congelados + OI diferido
    assert len(gw._ib_aux.requested) == 1 and len(gw.ib.requested) == 1


async def test_without_the_instruments_connection_the_main_one_serves_them():
    gw = gateway()
    gw._ib_aux = None
    gw.s = gw.s.model_copy(update={"connect_timeout_seconds": 0.2, "ports": gw.s.ports.model_copy(update={"paper": 1, "live": 1})})
    out = await gw.get_quotes([estx(5450.0, 1)])
    assert out[estx(5450.0, 1)].open_interest is None and out[estx(5450.0, 1)].bid == 10.0     # congelado: sin OI


async def test_index_price_does_not_wait_for_the_iv():
    gw = gateway()
    quotes = await gw.get_underlying_quotes(["ESTX50"])
    assert quotes["ESTX50"].price == 6068.22
    assert gw._ib_aux.requested[-1][1] == ""                 # sin tick 106


async def test_hybrid_sends_the_instrument_to_the_broker_and_the_rest_to_the_provider():
    inner = FakeGateway(connected=True, chains={"ESTX50": OptionChain("ESTX50", [E1], [5450.0], 10)}, prices={"ESTX50": 6068.0})
    provider = FakeOptionData()
    gw = HybridGateway(inner, provider, MarketDataSettings(), instruments=["ESTX50"])
    assert (await gw.get_option_chain("ESTX50")).multiplier == 10
    assert not any(c[0] == "get_put_listing" for c in provider.calls)       # el proveedor no se consulta
    assert await gw.get_underlying_price("ESTX50") == 6068.0
    assert await gw.get_days_to_ex_dividend_many(["ESTX50"]) == {}
    assert [c.ticker for c in await gw.qualify_contracts([estx()])] == ["ESTX50"]


async def test_hybrid_without_tws_cannot_serve_the_instrument():
    gw = HybridGateway(FakeGateway(connected=False), FakeOptionData(), MarketDataSettings(), instruments=["ESTX50"])
    with pytest.raises(DataUnavailableError):
        await gw.get_option_chain("ESTX50")
    assert await gw.get_underlying_price("ESTX50") is None


class _Row(dict):
    get = dict.get


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def iterrows(self):
        return iter(self.rows)


def test_yahoo_frame_becomes_ohlc_and_skips_empty_rows():
    rows = [(datetime(2026, 10, 22), _Row(Close=6087.0, High=6100.0, Low=6050.0)),
            (datetime(2026, 10, 23), _Row(Close=float("nan"), High=1.0, Low=1.0)),
            (datetime(2026, 10, 21), _Row(Close=6000.0, High=float("nan"), Low=5990.0))]
    assert ohlc_from_frame(_Frame(rows)) == [(date(2026, 10, 21), 6000.0, None, 5990.0), (date(2026, 10, 22), 6087.0, 6100.0, 6050.0)]
    assert ohlc_from_frame(None) == []


async def test_split_candles_send_the_index_to_yahoo_and_survive_its_failure():
    tasty = FakeCandles(bars={"AAPL": [(date(2026, 10, 22), 200.0)]})
    yahoo = FakeCandles(bars={"ESTX50": [(date(2026, 10, 22), 6087.0)]})
    split = SplitCandles(tasty, yahoo, ["ESTX50"])
    out = await split.get_daily_closes(["AAPL", "ESTX50"], 30)
    assert set(out) == {"AAPL", "ESTX50"} and tasty.calls == [["AAPL"]] and yahoo.calls == [["ESTX50"]]
    yahoo.error = RuntimeError("limitado")
    assert set(await split.get_daily_ohlc(["AAPL", "ESTX50"], 30)) == {"AAPL"}
