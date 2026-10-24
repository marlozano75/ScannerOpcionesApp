"""Sector sin TWS: código SIC de EDGAR, Yahoo Finance y el orden EDGAR → Yahoo → IBKR del `HybridGateway`."""
from datetime import datetime

import pytest

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.config.settings import MarketDataSettings
from scanner_opciones.marketdata.edgar import EdgarFinancialsProvider
from scanner_opciones.marketdata.options import FakeOptionData
from scanner_opciones.marketdata.sectors import ChainedSectors, sic_to_sector, yahoo_to_sector
from scanner_opciones.scanner.quality import is_exempt
from scanner_opciones.domain.models import TickerInfo


@pytest.mark.parametrize("sic,sector", [
    (3571, "Technology"), (7372, "Technology"), (6021, "Financial"), (6798, "Financial"), (6500, "Financial"), (1311, "Energy"), (2911, "Energy"),
    (4911, "Utilities"), (4953, "Industrial"), (1040, "Basic Materials"), (2834, "Consumer, Non-cyclical"), (5961, "Consumer, Cyclical"),
    (3674, "Technology"), (4813, "Communications"), (3711, "Consumer, Cyclical"), (3721, "Industrial")])
def test_sic_codes_map_to_ibkr_sector_names(sic, sector):
    assert sic_to_sector(sic) == sector
    assert sic_to_sector(str(sic)) == sector


def test_unknown_or_missing_sic_gives_nothing_and_yahoo_names_are_translated():
    assert sic_to_sector(None) is None and sic_to_sector("") is None and sic_to_sector(9999) is None
    assert yahoo_to_sector("Financial Services") == "Financial" and yahoo_to_sector("Real Estate") == "Financial"
    assert yahoo_to_sector("Basic Materials") == "Basic Materials" and yahoo_to_sector("nonsense") is None
    # los sectores traducidos siguen activando las exenciones de la calidad
    for text in ("Financial", "Energy", "Utilities", "Basic Materials"):
        assert is_exempt(TickerInfo("X", sector=text))
    assert not is_exempt(TickerInfo("X", sector="Technology"))


async def test_edgar_reads_the_sic_from_the_submissions():
    async def fetch(url):
        if "company_tickers" in url:
            return {"0": {"ticker": "BAC", "cik_str": 70858}, "1": {"ticker": "AAPL", "cik_str": 320193}}
        return {"sic": "6021", "sicDescription": "National Commercial Banks"} if "0000070858" in url else {"sic": "3571", "sicDescription": "Electronic Computers"}

    provider = EdgarFinancialsProvider("yo@example.com", fetch_json=fetch)
    found = await provider.get_sectors(["BAC", "AAPL", "NOPE"])
    assert found == {"BAC": ("Financial", "National Commercial Banks"), "AAPL": ("Technology", "Electronic Computers")}


class Fixed:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.asked = data or {}, error, []

    async def get_sectors(self, tickers):
        self.asked.append(list(tickers))
        if self.error:
            raise self.error
        return {t: self.data[t] for t in tickers if t in self.data}


async def test_chain_asks_each_source_only_for_what_is_missing_and_skips_failing_ones():
    edgar, yahoo = Fixed({"BAC": ("Financial", "Banks")}), Fixed({"TSM": ("Technology", None)})
    out = await ChainedSectors([Fixed(error=RuntimeError("caído")), edgar, yahoo]).get_sectors(["BAC", "TSM", "ZZZ"])
    assert out == {"BAC": ("Financial", "Banks"), "TSM": ("Technology", None)}
    assert edgar.asked == [["BAC", "TSM", "ZZZ"]] and yahoo.asked == [["TSM", "ZZZ"]]


def gateway(sectors, connected, ibkr=None):
    inner = FakeGateway(connected=connected, sectors=ibkr or {})
    return HybridGateway(inner, FakeOptionData(), MarketDataSettings(), now=lambda: datetime(2026, 10, 8, 12), sectors=sectors), inner


async def test_gateway_uses_the_free_sources_first_and_works_without_tws():
    src = Fixed({"BAC": ("Financial", "Banks")})
    gw, _ = gateway(ChainedSectors([src]), connected=False)
    assert await gw.get_sector_info("BAC") == ("Financial", "Banks")                 # sin TWS
    assert await gw.get_sector_info("BAC") == ("Financial", "Banks") and len(src.asked) == 1       # y de memoria la segunda vez
    assert await gw.get_sector_info("ZZZ") == (None, None)                           # nadie lo conoce y no hay TWS


async def test_gateway_falls_back_to_tws_only_when_the_free_sources_do_not_know_it():
    gw, _ = gateway(ChainedSectors([Fixed({"BAC": ("Financial", None)})]), connected=True, ibkr={"BAC": ("IBKR", None), "KO": ("Consumer, Non-cyclical", "Beverages")})
    assert await gw.get_sector_info("BAC") == ("Financial", None)
    assert await gw.get_sector_info("KO") == ("Consumer, Non-cyclical", "Beverages")
    assert await gw.prime_sectors(["BAC", "KO", "KO"]) == 0                          # BAC ya está en memoria; KO no lo resuelven las fuentes
