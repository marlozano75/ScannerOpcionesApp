"""IV Rank / Percentile desde tastytrade (puerto VolatilityProvider), sin historial de IBKR."""
import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings, load_settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.marketdata.volatility import FakeVolatility, IVMetrics
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, FixedMarket, make_service, seed_market


async def service_with(volatility):
    _, gw = make_service()
    seed_market(gw)
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True), volatility=volatility)
    await gw.connect()
    svc.watchlist.add(["AAPL"], NOW)
    return svc, gw


async def test_daily_update_uses_one_batch_request_for_all_tickers():
    vol = FakeVolatility({"AAPL": IVMetrics(iv_rank=61.0, iv_percentile=72.0)})
    svc, gw = await service_with(vol)
    await svc.daily.run_pending()
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.iv_percentile) == (61.0, 72.0)
    assert vol.calls == [["AAPL"]]


async def test_ticker_the_provider_does_not_cover_has_no_iv_stats():
    svc, gw = await service_with(FakeVolatility({}))
    report = await svc.daily.run_pending()
    assert report.errors == {}
    info = svc.ticker_info.get("AAPL")
    assert info.iv_rank is None and info.iv_percentile is None


async def test_provider_failure_does_not_fail_the_update():
    svc, gw = await service_with(FakeVolatility(error=VolatilityError("sin red")))
    report = await svc.daily.run_pending()
    assert report.errors == {} and report.updated == ["AAPL"]


async def test_refresh_uses_the_provider_values():
    vol = FakeVolatility({"AAPL": IVMetrics(iv_rank=10.0, iv_percentile=20.0)})
    svc, gw = await service_with(vol)
    await svc.daily.run_pending()
    vol.metrics["AAPL"] = IVMetrics(iv_rank=55.0, iv_percentile=65.0)
    await svc.refresh_job.run()
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.iv_percentile) == (55.0, 65.0)
    assert len(vol.calls) == 2


def test_credentials_are_never_in_repr():
    s = Settings(tastytrade={"client_secret": "a-secret", "refresh_token": "a-token"})
    assert "a-secret" not in repr(s) and "a-token" not in repr(s)


def test_the_real_config_file_still_loads():
    load_settings("config/config.yaml")
