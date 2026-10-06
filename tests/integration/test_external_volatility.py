"""IV Rank / Percentile desde un proveedor externo (tastytrade) con respaldo en el historial de IBKR."""
import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import ConfigError, Settings, load_settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.marketdata.volatility import FakeVolatility, IVMetrics
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, FixedMarket, make_service, seed_market


async def service_with(volatility):
    svc, gw = make_service()
    seed_market(gw)
    ext = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True), volatility=volatility)
    await gw.connect()
    ext.watchlist.add(["AAPL"], NOW)
    return ext, gw


async def test_external_metrics_replace_the_ibkr_iv_history():
    vol = FakeVolatility({"AAPL": IVMetrics(iv_rank=61.0, iv_percentile=72.0)})
    svc, gw = await service_with(vol)
    await svc.daily.run_pending()
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.iv_percentile) == (61.0, 72.0)
    assert not [c for c in gw.calls if c[0] == "get_iv_history"]        # no se descarga el historial
    assert vol.calls == [["AAPL"]]                                       # una petición en lote


async def test_tickers_the_provider_does_not_cover_fall_back_to_ibkr():
    svc, gw = await service_with(FakeVolatility({}))
    await svc.daily.run_pending()
    assert [c for c in gw.calls if c[0] == "get_iv_history"]
    assert svc.ticker_info.get("AAPL").iv_rank is not None


async def test_provider_failure_falls_back_to_ibkr_without_failing_the_update():
    svc, gw = await service_with(FakeVolatility(error=VolatilityError("sin red")))
    report = await svc.daily.run_pending()
    assert report.errors == {} and report.updated == ["AAPL"]
    assert [c for c in gw.calls if c[0] == "get_iv_history"]


async def test_refresh_uses_the_provider_values():
    vol = FakeVolatility({"AAPL": IVMetrics(iv_rank=10.0, iv_percentile=20.0)})
    svc, gw = await service_with(vol)
    await svc.daily.run_pending()
    vol.metrics["AAPL"] = IVMetrics(iv_rank=55.0, iv_percentile=65.0)
    await svc.refresh_job.run()
    info = svc.ticker_info.get("AAPL")
    assert (info.iv_rank, info.iv_percentile) == (55.0, 65.0)
    assert len(vol.calls) == 2


def test_tastytrade_source_requires_credentials():
    with pytest.raises(ValueError, match="client_secret"):
        Settings(iv={"source": "tastytrade"})
    ok = Settings(iv={"source": "tastytrade"}, tastytrade={"client_secret": "a", "refresh_token": "b"})
    assert ok.iv.source == "tastytrade"
    assert "refresh_token" not in repr(ok.tastytrade)                    # los secretos no salen en repr/logs


def test_the_real_config_file_still_loads():
    load_settings("config/config.yaml")
