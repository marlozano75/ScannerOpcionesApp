"""Si el precio de IBKR no cuadra con el de tastytrade se usa el de tastytrade (puerto PriceProvider)."""
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import PriceError
from scanner_opciones.domain.models import UnderlyingQuote
from scanner_opciones.marketdata.prices import FakePrices, reconcile_price, reconcile_quotes
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, FixedMarket, make_service, seed_market


@pytest.mark.parametrize("ibkr, external, expected", [
    (51.915, 40.095, 40.095),     # el caso CBNK: IBKR muy por encima
    (40.5, 40.095, 40.5),         # +1 %: se conserva el de IBKR
    (38.2, 40.095, 40.095),       # también por debajo: -4,7 % supera el 4 % de la prueba
    (None, 40.0, None),           # sin precio de IBKR no se inventa uno
    (40.0, None, 40.0),           # el proveedor no cubre el ticker
    (40.0, 0, 40.0),
])
def test_reconcile_price(ibkr, external, expected):
    got = reconcile_price("CBNK", ibkr, external, max_deviation_pct=4)
    assert got == expected


async def test_reconcile_quotes_replaces_only_the_odd_ones_and_keeps_iv():
    quotes = {"CBNK": UnderlyingQuote(51.9, 0.3), "KO": UnderlyingQuote(60.0, 0.2), "NOPE": UnderlyingQuote(None, 0.1)}
    prov = FakePrices({"CBNK": 40.1, "KO": 60.2, "NOPE": 5.0})
    out = await reconcile_quotes(prov, quotes, 5)
    assert out["CBNK"] == UnderlyingQuote(40.1, 0.3)
    assert out["KO"] == quotes["KO"] and out["NOPE"] == quotes["NOPE"]
    assert prov.calls == [["CBNK", "KO"]]            # una sola petición; sin precio de IBKR no se pide


async def test_provider_failure_keeps_ibkr_prices():
    quotes = {"CBNK": UnderlyingQuote(51.9)}
    assert await reconcile_quotes(FakePrices(error=PriceError("sin red")), quotes, 5) == quotes
    assert await reconcile_quotes(None, quotes, 5) == quotes


async def test_tastytrade_prices_use_last_then_mark_and_skip_the_rest():
    async def fetch_quotes(symbols):
        return [NS(symbol="AAPL", last=332.38, mark=332.4), NS(symbol="CBNK", last=None, mark=40.095),
                NS(symbol="EMPTY", last=None, mark=None), NS(symbol="OTHER", last=1, mark=1)]

    prov = TastytradeVolatility("s", "t", fetch_quotes=fetch_quotes)
    assert await prov.get_prices(["AAPL", "CBNK", "EMPTY"]) == {"AAPL": 332.38, "CBNK": 40.095}


async def test_tastytrade_price_error_is_a_price_error():
    async def boom(symbols):
        raise RuntimeError("401")

    with pytest.raises(PriceError):
        await TastytradeVolatility("s", "t", fetch_quotes=boom).get_prices(["AAPL"])


async def test_daily_update_and_refresh_store_the_external_price_when_ibkr_is_odd():
    _, gw = make_service()
    seed_market(gw)
    gw.prices["AAPL"] = 150.0                          # IBKR dice 150, tastytrade 100,2
    prov = FakePrices({"AAPL": 100.2})
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True), prices=prov)
    await gw.connect()
    svc.watchlist.add(["AAPL"], NOW)
    await svc.daily.run_pending()
    assert svc.ticker_info.get("AAPL").underlying_price == 100.2
    gw.prices["AAPL"] = 160.0
    await svc.refresh_all()
    assert svc.ticker_info.get("AAPL").underlying_price == 100.2
    gw.prices["AAPL"] = 100.5                          # dentro del 5 %: manda IBKR
    await svc.refresh_all()
    assert svc.ticker_info.get("AAPL").underlying_price == 100.5
