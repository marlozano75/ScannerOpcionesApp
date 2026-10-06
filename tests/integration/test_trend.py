"""Tendencia alcista (precio > SMA50 > SMA200) con velas diarias de tastytrade (puerto CandleProvider)."""
from datetime import date, timedelta

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import CandleError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.candles import FakeCandles
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, tasty_symbol
from scanner_opciones.metrics.trend import TrendStats, compute_trend, is_uptrend, sma
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, TODAY, FixedMarket, make_service, seed_market


def bars(closes, end=TODAY - timedelta(days=1)):
    """Una barra por día natural acabando en `end` (los días no importan, solo el orden)."""
    n = len(closes)
    return [(end - timedelta(days=n - 1 - i), c) for i, c in enumerate(closes)]


def test_sma():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1, 2, 3], 4) is None


def test_compute_trend_ignores_today_and_needs_the_long_window():
    series = bars([10.0] * 5 + [20.0] * 5)
    series.append((TODAY, 999.0))                      # barra en curso: no cuenta
    assert compute_trend(series, TODAY, 2, 10) == TrendStats(20.0, 15.0)
    assert compute_trend(series, TODAY, 2, 11) is None


@pytest.mark.parametrize("price, short, long_, expected", [
    (110, 100, 90, True), (95, 100, 90, False), (110, 90, 100, False),
    (None, 100, 90, None), (110, None, 90, None), (110, 100, None, None),
])
def test_is_uptrend(price, short, long_, expected):
    assert is_uptrend(price, short, long_) is expected
    assert TickerInfo("X", underlying_price=price, sma_short=short, sma_long=long_).uptrend is expected


def test_tasty_symbol():
    assert [tasty_symbol(t) for t in ("AAPL", "PBR-A", "BRK.B", "BRK B"[:3])] == ["AAPL", "PBR/A", "BRK/B", "BRK"]


async def test_get_daily_closes_maps_symbols_back_and_wraps_errors():
    seen = {}

    async def fetch_candles(symbols, days, timeout):
        seen["symbols"] = list(symbols)
        return {"PBR/A": [(TODAY, 1.0)], "OTHER": [(TODAY, 2.0)]}

    prov = TastytradeVolatility("s", "t", fetch_candles=fetch_candles)
    assert await prov.get_daily_closes(["PBR-A", "AAPL"], 400) == {"PBR-A": [(TODAY, 1.0)]}
    assert seen["symbols"] == ["PBR/A", "AAPL"]

    async def boom(symbols, days, timeout):
        raise RuntimeError("401")

    with pytest.raises(CandleError):
        await TastytradeVolatility("s", "t", fetch_candles=boom).get_daily_closes(["AAPL"], 400)


def settings(**trend):
    return Settings.model_validate({"trend": {"sma_short": 5, "sma_long": 20, **trend}})


async def daily(svc):
    """Actualización diaria pendiente + histórico de cierres (como hace `AppService.run_daily`)."""
    report = await svc.daily.run_pending()
    await svc.daily.update_history(svc.watchlist.list())
    return report


async def service_with(candles):
    _, gw = make_service()
    seed_market(gw)                                    # AAPL a 100
    svc = AppService(gw, Database(":memory:"), settings(), lambda: NOW, market=FixedMarket(True), candles=candles)
    await gw.connect()
    svc.watchlist.add(["AAPL"], NOW)
    return svc, gw


async def test_daily_update_stores_the_averages_and_the_ticker_is_in_uptrend():
    up = bars([50.0] * 10 + [90.0] * 20)               # SMA5 = SMA20 = 90
    candles = FakeCandles({"AAPL": up})
    svc, _ = await service_with(candles)
    await daily(svc)
    info = svc.ticker_info.get("AAPL")
    assert info.sma_short == 90.0 and info.sma_long == 90.0 and info.trend_at == NOW
    assert info.uptrend is False                       # 100 > 90 pero 90 no es > 90
    assert candles.calls == [["AAPL"]]                 # una sola petición para todos


async def test_uptrend_and_downtrend_flags():
    svc, _ = await service_with(FakeCandles({"AAPL": bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5)}))
    await daily(svc)
    info = svc.ticker_info.get("AAPL")
    assert info.sma_short == 95.0 > info.sma_long and info.uptrend is True      # 100 > 95 > media larga
    svc2, _ = await service_with(FakeCandles({"AAPL": bars([150.0] * 15 + [120.0] * 10)}))
    await daily(svc2)
    assert svc2.ticker_info.get("AAPL").uptrend is False


async def test_provider_failure_keeps_the_stored_trend():
    svc, _ = await service_with(FakeCandles({"AAPL": bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5, end=TODAY - timedelta(days=3))}))
    await daily(svc)
    svc.daily.candles = FakeCandles(error=CandleError("sin red"))
    svc.watchlist.mark_daily_updated("AAPL", NOW - timedelta(days=1))
    report = await daily(svc)
    assert svc.daily.candles.calls == [["AAPL"]]       # se intentó pedir lo que faltaba
    assert report.errors == {} and svc.ticker_info.get("AAPL").sma_short == 95.0


async def rerun(svc, candles):
    """Otra actualización diaria con otro proveedor de velas (como al día siguiente)."""
    svc.daily.candles = candles
    svc.watchlist.mark_daily_updated("AAPL", NOW - timedelta(days=1))
    await daily(svc)
    return candles


async def test_first_run_downloads_the_whole_history_and_stores_it():
    series = bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5, end=TODAY - timedelta(days=3))
    candles = FakeCandles({"AAPL": series + [(TODAY, 999.0)]})
    svc, _ = await service_with(candles)
    await daily(svc)
    assert candles.days == [settings().trend.history_days]
    assert sorted(svc.bars.closes("AAPL").items()) == series        # la barra de hoy (en curso) no se guarda


async def test_next_day_only_asks_for_the_missing_days():
    old = bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5, end=TODAY - timedelta(days=4))
    svc, _ = await service_with(FakeCandles({"AAPL": old}))
    await daily(svc)
    new = [(TODAY - timedelta(days=3), 96.0), (TODAY - timedelta(days=2), 97.0), (TODAY - timedelta(days=1), 98.0)]
    candles = await rerun(svc, FakeCandles({"AAPL": old[-5:] + new}))
    assert candles.days == [4 + 7]                                   # días desde el último guardado + solape
    assert len(svc.bars.closes("AAPL")) == len(old) + 3
    assert svc.ticker_info.get("AAPL").sma_short == pytest.approx((95 + 96 + 97 + 98 + 95) / 5)


async def test_up_to_date_history_makes_no_request():
    svc, _ = await service_with(FakeCandles({"AAPL": bars([50.0] * 30)}))     # último cierre: ayer
    await daily(svc)
    candles = await rerun(svc, FakeCandles({"AAPL": []}))
    assert candles.calls == []
    assert svc.ticker_info.get("AAPL").sma_short == 50.0                      # las medias salen de lo guardado


async def test_adjusted_history_is_discarded_and_downloaded_again():
    old = bars([100.0] * 30, end=TODAY - timedelta(days=4))
    svc, _ = await service_with(FakeCandles({"AAPL": old}))
    await daily(svc)
    adjusted = bars([50.0] * 30, end=TODAY - timedelta(days=1))               # split 2:1: todo el pasado cambia
    candles = await rerun(svc, FakeCandles({"AAPL": adjusted}))
    assert candles.days == [4 + 7, settings().trend.history_days]             # incremental y luego completo
    assert set(svc.bars.closes("AAPL").values()) == {50.0}


async def test_old_bars_are_pruned_and_orphans_removed():
    cfg = settings(history_days=40)
    svc, _ = await service_with(FakeCandles({"AAPL": bars([50.0] * 60)}))
    svc.settings = cfg
    svc.daily.settings = cfg
    await daily(svc)
    assert min(svc.bars.closes("AAPL")) >= TODAY - timedelta(days=40)
    svc.watchlist.remove("AAPL")
    assert svc.cleanup_orphans()["daily_bars"] > 0 and svc.bars.closes("AAPL") == {}


async def test_scan_filter_only_uptrend():
    svc, gw = await service_with(FakeCandles({"AAPL": bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5)}))
    await daily(svc)
    from scanner_opciones.domain.models import OptionQuote
    for c in svc.contracts.list():
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
    await svc.refresh_all()
    crit = svc.criteria().with_filters(strike_below_pct_min=1, min_annual_yield_pct=0, dte_min=1, dte_max=45)
    assert svc.scan(crit).results
    svc.ticker_info.update_trend("AAPL", 120.0, 90.0, NOW)            # el precio (100) ya no supera la media corta
    assert svc.scan(crit).results                                     # sin marcar el filtro no cambia nada
    out = svc.scan(crit.with_filters(only_uptrend=True))
    assert not out.results and out.rejected_count > 0


def test_trend_settings_validate():
    with pytest.raises(ValueError):
        Settings.model_validate({"trend": {"sma_short": 200, "sma_long": 50}})


async def test_history_is_downloaded_even_when_the_daily_update_has_nothing_pending():
    """Regresión: el histórico dependía de que el ticker estuviera pendiente de la actualización diaria de hoy,
    así que tras vaciarlo (migración) los filtros técnicos descartaban todo hasta el día siguiente."""
    candles = FakeCandles({"AAPL": bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5)})
    svc, _ = await service_with(candles)
    await svc.daily.run_pending()                      # la actualización diaria de hoy ya está hecha ...
    assert svc.watchlist.pending_daily_update(NOW.date()) == [] and svc.bars.closes("AAPL") == {}
    assert svc.history_coverage() == (0, 1)
    await svc.run_daily(wait=True)                     # ... y aun así el histórico se completa
    assert svc.history_coverage() == (1, 1) and len(svc.bars.closes("AAPL")) == 25
    assert svc.ticker_info.get("AAPL").sma_short == 95.0
