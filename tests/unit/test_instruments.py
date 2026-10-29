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


# ---- fase 2: importes en euros ----------------------------------------------------------------------------------
from dataclasses import replace as _replace

from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.scanner.criteria import ScanCriteria, criteria_from_settings
from scanner_opciones.scanner.currency import Nlv, usd_rate
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.quality import ticker_quality_reject

TODAY = date(2026, 10, 26)


def test_usd_rate_reads_the_rate_of_the_nlv_and_defaults_to_one():
    nlv = Nlv(40000.0, {"ESTX50": 1.1})
    assert float(nlv) == 40000.0 and nlv + 1 == 40001.0
    assert usd_rate(nlv, "ESTX50") == 1.1 and usd_rate(nlv, "AAPL") == 1.0
    assert usd_rate(40000.0, "ESTX50") == 1.0 and usd_rate(None, "ESTX50") == 1.0


def snap(ticker, strike, bid, ask, mult, **kw):
    return ContractSnapshot(OptionContract(ticker, date(2026, 11, 20), strike, multiplier=mult), updated_at=datetime(2026, 10, 26, 12), bid=bid, ask=ask,
                            open_interest=500, bid_size=10, **kw)


def info(ticker, price):
    return TickerInfo(ticker, underlying_price=price)


def scan(criteria, nlv, snaps, infos):
    return run_scan(snaps, infos, [], criteria, TODAY, nlv_usd=nlv, exempt_sectors=("=ESTX50", "~ESTX50"))


def test_minimum_credit_and_capital_at_risk_are_compared_in_dollars():
    base = _replace(criteria_from_settings(Settings()), min_yield_pct=0.0, strike_below_pct_min=5.0, strike_below_pct_max=40.0, dte_min=1, dte_max=60,
                    min_oi=None, min_bid_size=None, max_spread_pct=None, min_iv_rank=None, min_iv_percentile=None)
    # put 5450 de ESTX50 a 18,4–18,8 €: cobras 18,6 × 10 = 186 €; con 1,1 $/€ son 205 $
    s = [snap("ESTX50", 5450.0, 18.4, 18.8, 10)]
    i = {"ESTX50": info("ESTX50", 6068.22)}
    ok = scan(base.with_filters(min_put_credit=200.0, price_reference=base.price_reference), Nlv(40000.0, {"ESTX50": 1.1}), s, i)
    no_fx = scan(base.with_filters(min_put_credit=200.0), Nlv(40000.0), s, i)
    assert len(ok.results) == 1 and len(no_fx.results) == 0               # 205 $ ≥ 200 con el cambio; 186 < 200 sin él
    # capital en riesgo: (5450 − 18,6) × 10 € = 54.314 € = 59.746 $ → 149 % de un NLV de 40.000 $; con el 100 % máximo no pasa, con el 200 % sí
    assert len(scan(base.with_filters(max_capital_risk_pct=100.0), Nlv(40000.0, {"ESTX50": 1.1}), s, i).results) == 0
    assert len(scan(base.with_filters(max_capital_risk_pct=200.0), Nlv(40000.0, {"ESTX50": 1.1}), s, i).results) == 1


def test_the_index_has_its_own_spread_widths_in_points():
    crit = criteria_from_settings(Settings())
    assert dict(crit.instrument_widths)["ESTX50"] == (50.0, 100.0, 150.0, 200.0, 250.0, 300.0)
    base = _replace(crit, min_yield_pct=0.0, strike_below_pct_min=5.0, strike_below_pct_max=40.0, dte_min=1, dte_max=60, min_oi=None,
                    min_bid_size=None, max_spread_pct=None, min_iv_rank=None, min_iv_percentile=None, spread_mode=True, spread_widths=(1.0, 5.0),
                    best_width=False, max_combo_spread_pct=None, min_credit_width_pct=None, min_return_on_risk_pct=None, min_spread_credit=None,
                    min_long_oi=None, max_long_spread_pct=None)
    s = [snap("ESTX50", 5450.0, 18.4, 18.8, 10), snap("ESTX50", 5350.0, 9.0, 9.4, 10), snap("ESTX50", 5400.0, 12.0, 12.4, 10)]
    out = scan(base, Nlv(40000.0, {"ESTX50": 1.1}), s, {"ESTX50": info("ESTX50", 6068.22)})
    pairs = {(sp.short.snapshot.contract.strike, sp.long.contract.strike, sp.width) for sp in out.spreads}
    assert (5450.0, 5400.0, 50.0) in pairs and (5450.0, 5350.0, 100.0) in pairs     # anchos en puntos, no los de las acciones (1–20 $)


def test_the_index_is_exempt_from_the_option_liquidity_of_tastytrade():
    crit = criteria_from_settings(Settings()).with_filters(min_option_liquidity=3)
    own = TickerInfo("ESTX50")
    assert ticker_quality_reject(own, crit, ("=ESTX50", "~ESTX50")) is None
    assert ticker_quality_reject(own, crit, ("=ESTX50",)) is not None             # sin la marca «~» sí lo pide (un ETF normal)


# ---- fase 3: posiciones de Eurex en el Panel ---------------------------------------------------------------------
from dataclasses import replace as _rep

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.domain.models import Position
from scanner_opciones.portfolio.diversification import nominal
from scanner_opciones.portfolio.leverage import long_put_protection, short_put_exposure
from scanner_opciones.portfolio.management import open_risk, ticker_weights


def short_estx(fx=1.1, strike=5450.0, qty=-1.0, hedge=None, avg_cost=-186.0):
    return Position("ESTX50", qty, -150.0, "Índice Europa", OptionContract("ESTX50", E1, strike, multiplier=10), avg_cost, hedge, fx)


def test_nominal_exposure_of_an_index_put_is_converted_to_dollars():
    p = short_estx()
    assert nominal(p) == pytest.approx(5450.0 * 10 * 1.1)                    # 59.950 $
    assert nominal(_rep(p, fx=1.0)) == 54500.0
    assert short_put_exposure([p, short_estx(strike=5000.0, qty=-2.0)]) == pytest.approx((5450.0 * 10 + 5000.0 * 10 * 2) * 1.1)
    assert long_put_protection([short_estx(qty=1.0)]) == pytest.approx(5450.0 * 10 * 1.1)


def test_spread_width_weight_and_open_risk_use_the_exchange_rate():
    spread = short_estx(hedge=5350.0, avg_cost=-40.0)
    assert nominal(spread) == pytest.approx(100.0 * 10 * 1.1)                # el ancho, en dólares
    assert open_risk([spread]) == pytest.approx(100.0 * 10 * 1.1 - 40.0 * 1.1)   # ancho − crédito, en dólares
    w = ticker_weights([short_estx()], 44000.0, ManagementSettings())[0]
    assert abs(w.pct_nlv - 5450.0 * 10 * 1.1 / 44000.0 * 100) < 1e-9


async def test_the_service_puts_the_rate_on_the_index_positions(tmp_path):
    from scanner_opciones.app.service import AppService
    from scanner_opciones.broker.fake_gateway import FakeGateway
    from scanner_opciones.domain.models import AccountSummary
    from scanner_opciones.storage.db import Database
    svc = AppService(FakeGateway(connected=True), Database(tmp_path / "app.db"), Settings(), lambda: datetime(2026, 10, 26, 12))
    svc.state.account = AccountSummary("U1", net_liquidation=40000.0, base_currency="EUR", usd_per_base=1.1)
    aapl = Position("AAPL", -1.0, -100.0, None, OptionContract("AAPL", E1, 200.0))
    out = svc._with_fx([short_estx(fx=1.0), aapl])
    assert [p.fx for p in out] == [1.1, 1.0]


def test_calendario_propio_del_instrumento():
    """Eurex (8:00–22:00 de Berlín) está abierto a las 10:50 españolas aunque el mercado de EE. UU. no lo esté."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from scanner_opciones.config.settings import default_instruments
    from scanner_opciones.market.hours import MarketCalendar
    cal = MarketCalendar.for_instrument(default_instruments()["ESTX50"])
    berlin = ZoneInfo("Europe/Berlin")
    assert cal.is_open(datetime(2026, 10, 29, 10, 50, tzinfo=berlin))
    assert not cal.is_open(datetime(2026, 10, 29, 22, 5, tzinfo=berlin))
    assert not cal.is_open(datetime(2026, 10, 31, 12, 0, tzinfo=berlin))     # sábado


# ---- valores europeos dados de alta desde el Universo (resolución con TWS) ----
def _desc(symbol, sec="STK", cur="EUR", primary="IBIS", deriv=("OPT",)):
    return NS(contract=NS(symbol=symbol, secType=sec, currency=cur, primaryExchange=primary, exchange="SMART", conId=1), derivativeSecTypes=list(deriv))


def _chain(exchange="EUREX", tclass="SAP", mult="100", exps=3, strikes=5):
    return NS(exchange=exchange, tradingClass=tclass, multiplier=mult, expirations=["20261218"] * exps, strikes=[100.0] * strikes)


def test_european_candidates_keep_only_non_usd_valid_exchange_with_options():
    from scanner_opciones.config.settings import default_exchanges
    descs = [_desc("SAP", cur="USD", primary="NYSE"), _desc("SAP", deriv=()), _desc("SAP"), _desc("SAPX"), _desc("SAP", primary="LSEETF")]
    found = m.european_candidates(descs, "SAP", default_exchanges())
    assert len(found) == 1 and found[0].primaryExchange == "IBIS"


def test_european_stock_gets_its_exchange_hours_and_yahoo_suffix_and_the_eurex_chain():
    from scanner_opciones.config.settings import default_exchanges
    ex = default_exchanges()
    chain = m.pick_european_chain([_chain("SMART", exps=9), _chain("EUREX", exps=3)], ex)
    assert chain.exchange == "EUREX"
    inst = m.instrument_from_ib(_desc("SAP").contract, chain, ex)
    assert (inst.kind, inst.symbol, inst.exchange, inst.currency, inst.multiplier, inst.trading_class) == ("stock", "SAP", "EUREX", "EUR", 100, "SAP")
    assert (inst.listing_exchange, inst.yahoo_symbol, inst.timezone, str(inst.close)) == ("IBIS", "SAP.DE", "Europe/Berlin", "17:30:00")


def test_european_index_uses_the_derivatives_exchange_hours_and_has_no_yahoo_symbol():
    from scanner_opciones.config.settings import default_exchanges
    ex = default_exchanges()
    contract = _desc("DAX", sec="IND", primary="EUREX").contract
    inst = m.instrument_from_ib(contract, _chain("EUREX", "ODAX", "5"), ex)
    assert (inst.kind, inst.multiplier, inst.yahoo_symbol, str(inst.close), inst.listing_exchange) == ("index", 5, None, "22:00:00", None)


def test_meff_options_of_spanish_companies_are_accepted_and_preferred_on_a_tie():
    from scanner_opciones.config.settings import default_exchanges
    ex = default_exchanges()
    assert "MEFFRV" in ex
    chain = m.pick_european_chain([_chain("EUREX", "BSD2", exps=3), _chain("MEFFRV", "BBVAM", exps=3)], ex)
    assert chain.exchange == "MEFFRV"      # a igualdad de vencimientos gana MEFF, sea cual sea el orden de IBKR
    assert m.pick_european_chain([_chain("MEFFRV", "BBVAM", exps=3), _chain("EUREX", "BSD2", exps=5)], ex).exchange == "EUREX"
