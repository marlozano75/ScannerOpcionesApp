"""HybridGateway: cadena, cotizaciones, precios y ex-dividendos del proveedor; el resto, del broker interior."""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.config.settings import MarketDataSettings
from scanner_opciones.domain.errors import DataUnavailableError, OptionDataError, PriceError, VolatilityError
from scanner_opciones.domain.models import OptionChain, OptionContract, OptionQuote, UnderlyingQuote
from scanner_opciones.marketdata import tastytrade as tt
from scanner_opciones.marketdata.options import FakeOptionData, MarketExtras, listing_key
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility

TODAY = date(2026, 10, 8)
E1, E2 = date(2026, 10, 16), date(2026, 10, 23)


def contract(strike, expiry=E1, ticker="AAPL", con_id=None):
    return OptionContract(ticker, expiry, strike, con_id=con_id)


def listing(*pairs):
    return {listing_key(e, k): f".AAPL{e:%y%m%d}P{k:g}" for e, k in pairs}


def build(provider=None, inner=None, ttl_clock=None):
    inner = inner or FakeGateway(connected=True)
    provider = provider or FakeOptionData()
    clock = ttl_clock or (lambda: 0.0)
    return HybridGateway(inner, provider, MarketDataSettings(), now=lambda: datetime(2026, 10, 8, 12), clock=clock), inner, provider


async def test_chain_comes_from_the_provider_and_is_cached():
    gw, inner, prov = build(FakeOptionData(listings={"AAPL": listing((E1, 200), (E1, 210), (E2, 200))}))
    chain = await gw.get_option_chain("AAPL")
    assert chain == OptionChain("AAPL", [E1, E2], [200.0, 210.0], 100)
    await gw.get_option_chain("AAPL")
    assert [c for c in prov.calls if c[0] == "get_put_listing"] == [("get_put_listing", "AAPL")]   # una sola petición


async def test_listing_cache_expires():
    now = [0.0]
    gw, _, prov = build(FakeOptionData(listings={"AAPL": listing((E1, 200))}), ttl_clock=lambda: now[0])
    await gw.get_option_chain("AAPL")
    now[0] = MarketDataSettings().listing_ttl_minutes * 60 + 1
    await gw.get_option_chain("AAPL")
    assert len([c for c in prov.calls if c[0] == "get_put_listing"]) == 2


async def test_empty_chain_is_unavailable():
    gw, _, _ = build()
    with pytest.raises(DataUnavailableError):
        await gw.get_option_chain("UI")


async def test_chain_falls_back_to_the_broker_when_the_provider_fails():
    inner = FakeGateway(connected=True, chains={"AAPL": OptionChain("AAPL", [E1], [200.0])})
    gw, _, _ = build(FakeOptionData(error=OptionDataError("caído")), inner)
    assert (await gw.get_option_chain("AAPL")).strikes == [200.0]


async def test_qualify_keeps_only_listed_contracts_without_con_id():
    gw, _, _ = build(FakeOptionData(listings={"AAPL": listing((E1, 200), (E2, 210))}))
    got = await gw.qualify_contracts([contract(200), contract(210), contract(210, E2), contract(205)])
    assert got == [contract(200), contract(210, E2)]
    assert all(c.con_id is None for c in got)


async def test_qualify_falls_back_to_the_broker():
    inner = FakeGateway(connected=True)
    gw, _, _ = build(FakeOptionData(error=OptionDataError("caído")), inner)
    assert await gw.qualify_contracts([contract(200)]) == [contract(200)]


async def test_quotes_map_back_to_the_requested_contracts():
    prov = FakeOptionData(
        listings={"AAPL": listing((E1, 200), (E1, 210))},
        quotes={".AAPL261016P200": OptionQuote(bid=1.0, ask=1.2, iv=0.3, delta=-0.2, open_interest=50, bid_size=7)},
    )
    gw, _, _ = build(prov)
    stored = contract(200, con_id=123)                     # el guardado puede traer con_id de una versión anterior
    out = await gw.get_quotes([stored, contract(210), contract(215)])
    assert out == {stored: OptionQuote(bid=1.0, ask=1.2, iv=0.3, delta=-0.2, open_interest=50, bid_size=7)}
    assert prov.calls[-1] == ("get_option_quotes", (".AAPL261016P200", ".AAPL261016P210"))   # el 215 no existe: no se pide


async def test_quotes_fall_back_to_the_broker():
    c = contract(200)
    q = OptionQuote(bid=0.5, ask=0.6)
    inner = FakeGateway(connected=True, quotes={c: q})
    prov = FakeOptionData(listings={"AAPL": listing((E1, 200))}, error=OptionDataError("DXLink caído"))
    gw, _, _ = build(prov, inner)
    # el error salta al pedir la lista (mismo proveedor): se usa IBKR en cuanto el proveedor no responde
    assert await gw.get_quotes([c]) == {c: q}


async def test_quotes_fall_back_when_only_streaming_fails():
    c = contract(200)
    q = OptionQuote(bid=0.5, ask=0.6)
    inner = FakeGateway(connected=True, quotes={c: q})

    class OnlyQuotesFail(FakeOptionData):
        async def get_option_quotes(self, symbols):
            raise OptionDataError("DXLink caído")

    gw, _, _ = build(OnlyQuotesFail(listings={"AAPL": listing((E1, 200))}), inner)
    assert await gw.get_quotes([c]) == {c: q}


async def test_underlying_quotes_combine_price_and_iv_and_complete_with_the_broker():
    inner = FakeGateway(connected=True, prices={"MSFT": 400.0}, underlying_ivs={"MSFT": 0.25})
    prov = FakeOptionData(prices={"AAPL": 337.0}, extras={"AAPL": MarketExtras(iv30=0.28)})
    gw, _, _ = build(prov, inner)
    out = await gw.get_underlying_quotes(["AAPL", "MSFT", "NOPE"])
    assert out == {"AAPL": UnderlyingQuote(337.0, 0.28), "MSFT": UnderlyingQuote(400.0, 0.25)}


async def test_underlying_quotes_fall_back_when_prices_fail():
    inner = FakeGateway(connected=True, prices={"AAPL": 335.0})
    gw, _, _ = build(FakeOptionData(error=PriceError("caído")), inner)
    assert await gw.get_underlying_quotes(["AAPL"]) == {"AAPL": UnderlyingQuote(335.0, None)}


async def test_underlying_price():
    inner = FakeGateway(connected=True, prices={"MSFT": 400.0})
    gw, _, _ = build(FakeOptionData(prices={"AAPL": 337.0}), inner)
    assert await gw.get_underlying_price("AAPL") == 337.0
    assert await gw.get_underlying_price("MSFT") == 400.0     # el proveedor no lo conoce: IBKR


async def test_ex_dividend_days():
    prov = FakeOptionData(extras={
        "MU": MarketExtras(ex_dividend=date(2026, 10, 14)),
        "AAPL": MarketExtras(ex_dividend=date(2026, 8, 10)),   # ya pasó
        "TODAY": MarketExtras(ex_dividend=TODAY),
        "NODIV": MarketExtras(),
    })
    inner = FakeGateway(connected=True, ex_dividend_days={"KO": 12})
    gw, _, _ = build(prov, inner)
    out = await gw.get_days_to_ex_dividend_many(["MU", "AAPL", "TODAY", "NODIV", "KO"])
    assert out == {"MU": 6, "AAPL": None, "TODAY": 0, "NODIV": None, "KO": 12}   # KO, desconocido: IBKR
    assert await gw.get_days_to_ex_dividend("MU") == 6


async def test_ex_dividend_falls_back_when_the_provider_fails():
    inner = FakeGateway(connected=True, ex_dividend_days={"KO": 12})
    gw, _, _ = build(FakeOptionData(error=VolatilityError("caído")), inner)
    assert await gw.get_days_to_ex_dividend_many(["KO"]) == {"KO": 12}


async def test_account_margin_and_vix_stay_with_the_broker():
    c = contract(200)
    inner = FakeGateway(connected=True, margins={c: 500.0})
    gw, _, _ = build(inner=inner)
    assert gw.is_connected()
    assert await gw.what_if_margin(c, 2) == 1000.0
    await gw.disconnect()
    assert not inner.is_connected()


# ---- proveedor real con las llamadas al SDK sustituidas ---------------------------------------------------
def provider_with(**kw):
    return TastytradeVolatility("secret", "token", **kw)


async def test_provider_wraps_sdk_failures_as_option_data_errors():
    async def boom(*a):
        raise RuntimeError("sin red")

    p = provider_with(fetch_chain=boom, fetch_option_quotes=boom)
    with pytest.raises(OptionDataError):
        await p.get_put_listing("AAPL")
    with pytest.raises(OptionDataError):
        await p.get_option_quotes([".AAPL261016P200"])


async def test_provider_uses_tastytrade_symbols_for_classes_and_skips_empty_quote_requests():
    seen = []

    async def chain(symbol):
        seen.append(symbol)
        return {}

    p = provider_with(fetch_chain=chain)
    await p.get_put_listing("PBR-A")
    assert seen == ["PBR/A"] and await p.get_option_quotes([]) == {}


async def test_market_extras_are_read_from_the_metrics():
    async def fetch(symbols):
        return [NS(symbol="PBR/A", implied_volatility_index="0.4659", dividend_ex_date=date(2026, 8, 25)),
                NS(symbol="BAC", implied_volatility_index=None, dividend_ex_date=None)]

    out = await provider_with(fetch=fetch).get_market_extras(["PBR-A", "BAC"])
    assert out == {"PBR-A": MarketExtras(0.4659, date(2026, 8, 25)), "BAC": MarketExtras(None, None)}


async def test_market_extras_failure_is_a_volatility_error():
    async def fetch(symbols):
        raise RuntimeError("sin red")

    with pytest.raises(VolatilityError):
        await provider_with(fetch=fetch).get_market_extras(["AAPL"])


# ---- VIX, lotes de respaldo y cotizaciones en grupos ---------------------------------------------------------
from scanner_opciones.domain.models import VixData   # noqa: E402


async def test_vix_comes_from_the_provider():
    vix = VixData(15.6, [(date(2026, 10, 7), 15.4), (date(2026, 10, 8), 15.58)], [(date(2026, 10, 21), 17.67)], None)
    gw, inner, prov = build(FakeOptionData(vix=vix), FakeGateway(connected=True, vix=VixData(99.0)))
    out = await gw.get_vix_data(5, 3)
    assert out.current == 15.6 and out.futures == [(date(2026, 10, 21), 17.67)]
    assert out.updated_at == datetime(2026, 10, 8, 12)        # la hora de la aplicación si el proveedor no la da
    assert prov.calls[-1] == ("get_vix", 5, 3)


async def test_vix_falls_back_to_the_broker_on_failure_or_empty_answer():
    inner = FakeGateway(connected=True, vix=VixData(18.0, [(date(2026, 10, 8), 18.0)]))
    gw, _, _ = build(FakeOptionData(error=OptionDataError("caído")), inner)
    assert (await gw.get_vix_data(5, 3)).current == 18.0
    gw2, _, _ = build(FakeOptionData(), inner)                 # el proveedor responde sin datos
    assert (await gw2.get_vix_data(5, 3)).current == 18.0


async def test_fallback_quotes_go_to_the_broker_in_small_batches():
    cs = [contract(float(k)) for k in range(200, 207)]
    seen = []

    class Spy(FakeGateway):
        async def get_quotes(self, contracts):
            seen.append(len(contracts))
            return await super().get_quotes(contracts)

    inner = Spy(connected=True, quotes={c: OptionQuote(bid=1.0, ask=1.1) for c in cs})
    gw = HybridGateway(inner, FakeOptionData(error=OptionDataError("caído")), MarketDataSettings(), fallback_batch=3)
    assert len(await gw.get_quotes(cs)) == 7 and seen == [3, 3, 1]


def test_quote_batch_size_is_exposed_for_the_refresh():
    gw, _, _ = build()
    assert gw.quote_batch_size == MarketDataSettings().quote_batch_size == 2500


async def test_provider_vix_wraps_failures_and_returns_the_fetch():
    expected = VixData(15.0, [(date(2026, 10, 8), 15.0)])

    async def ok(history_days, futures_ahead):
        return expected

    async def boom(history_days, futures_ahead):
        raise RuntimeError("sin red")

    assert await provider_with(fetch_vix=ok).get_vix(5, 3) is expected
    with pytest.raises(OptionDataError):
        await provider_with(fetch_vix=boom).get_vix(5, 3)
