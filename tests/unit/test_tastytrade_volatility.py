from types import SimpleNamespace as NS

import pytest

from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.marketdata import tastytrade as tt
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility
from scanner_opciones.marketdata.volatility import IVMetrics


def item(symbol, rank, pct):
    return NS(symbol=symbol, implied_volatility_index_rank=rank, implied_volatility_percentile=pct)


def provider(fetch):
    return TastytradeVolatility("secret", "token", fetch=fetch)


async def test_values_are_converted_from_fractions_to_percent():
    async def fetch(symbols):
        return [item("AAPL", "0.508396063", "0.408245346")]

    assert await provider(fetch).get_iv_metrics(["AAPL"]) == {"AAPL": IVMetrics(50.84, 40.82)}


async def test_symbols_without_data_are_left_out():
    async def fetch(symbols):
        return [item("HPQ", 0.47, None), item("EMPTY", None, None), item("OTHER", 0.1, 0.1)]

    out = await provider(fetch).get_iv_metrics(["HPQ", "EMPTY", "PBR-A"])
    assert out == {"HPQ": IVMetrics(47.0, None)}          # sin dato ni rank ni percentil, o no pedido: fuera


async def test_requests_are_split_in_batches(monkeypatch):
    monkeypatch.setattr(tt, "BATCH_SIZE", 2)
    calls = []

    async def fetch(symbols):
        calls.append(list(symbols))
        return [item(s, 0.5, 0.5) for s in symbols]

    out = await provider(fetch).get_iv_metrics(["A", "B", "C"])
    assert calls == [["A", "B"], ["C"]] and set(out) == {"A", "B", "C"}


async def test_sdk_errors_become_volatility_errors_without_leaking_secrets():
    async def fetch(symbols):
        raise RuntimeError("401 unauthorized")

    with pytest.raises(VolatilityError, match="401") as exc:
        await provider(fetch).get_iv_metrics(["AAPL"])
    assert "secret" not in str(exc.value) and "token" not in str(exc.value)


async def test_no_tickers_makes_no_request():
    async def fetch(symbols):
        raise AssertionError("no debe llamar")

    assert await provider(fetch).get_iv_metrics([]) == {}


async def test_class_share_tickers_are_sent_with_a_slash_and_come_back_with_the_app_ticker():
    asked = []

    async def fetch(symbols):
        asked.append(list(symbols))
        return [item("PBR/A", 0.37, 0.2), item("BRK/B", 0.27, 0.1)]

    out = await provider(fetch).get_iv_metrics(["PBR-A", "BRK.B", "KO"])
    assert asked == [["PBR/A", "BRK/B", "KO"]]
    assert out == {"PBR-A": IVMetrics(37.0, 20.0), "BRK.B": IVMetrics(27.0, 10.0)}


async def test_prices_of_class_share_tickers_use_the_same_translation():
    asked = []

    async def fetch_quotes(symbols):
        asked.append(list(symbols))
        return [NS(symbol="PBR/A", last=21.73, mark=21.7)]

    prov = TastytradeVolatility("s", "t", fetch_quotes=fetch_quotes)
    assert await prov.get_prices(["PBR-A", "KO"]) == {"PBR-A": 21.73}
    assert asked == [["PBR/A", "KO"]]


async def test_puts_and_calls_with_injected_fetchers_are_requested_separately_and_marked():
    calls = []

    async def puts(symbol):
        calls.append(("P", symbol))
        return {("e", 1.0): "p"}

    async def cals(symbol):
        calls.append(("C", symbol))
        return {("e", 2.0): "c"}

    from scanner_opciones.marketdata.tastytrade import TastytradeVolatility
    p = TastytradeVolatility("s", "t", fetch_chain=puts, fetch_call_chain=cals)
    assert await p.get_option_listings("BRK-B") == ({("e", 1.0): "p"}, {("e", 2.0): "c"})
    assert calls == [("P", "BRK/B"), ("C", "BRK/B")]


async def test_vix_futures_list_is_requested_once_and_reused(monkeypatch):
    """`instruments/futures` se pedía en cada lectura del VIX (cada minuto): la lista de futuros cambia una vez al mes."""
    import sys
    import types
    from datetime import date, timedelta

    from scanner_opciones.marketdata.tastytrade import TastytradeVolatility

    requested = []
    soon, later = date.today() + timedelta(days=10), date.today() + timedelta(days=40)

    class Fut:
        def __init__(self, exp, sym):
            self.expiration_date, self.streamer_symbol, self.active = exp, sym, True

    class Future:
        @staticmethod
        async def get(session, product_codes):
            requested.append(product_codes)
            return [Fut(later, "/VXZ6"), Fut(soon, "/VXX6")]

    class Trade:
        pass

    class Streamer:
        def __init__(self, session):
            self.symbols = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, kind, symbols):
            self.symbols = list(symbols)

        async def listen(self, kind):
            for s in self.symbols:
                yield types.SimpleNamespace(event_symbol=s, price=20.0)

    for name, attrs in (("tastytrade.dxfeed", {"Trade": Trade}), ("tastytrade.instruments", {"Future": Future}), ("tastytrade.streamer", {"DXLinkStreamer": Streamer})):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    async def candles(symbols, days, timeout):
        return {"VIX": []}

    prov = TastytradeVolatility("s", "t", fetch_candles=candles)
    monkeypatch.setattr(prov, "_open_session", lambda: object())
    first = await prov.get_vix(5, 2)
    second = await prov.get_vix(5, 1)
    assert len(requested) == 1                                       # una sola petición de la lista para las dos lecturas
    assert [e for e, _ in first.futures] == [soon, later] and [e for e, _ in second.futures] == [soon]
