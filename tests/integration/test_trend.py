"""Histórico de cierres diarios de tastytrade (puerto CandleProvider) guardado en la base de datos."""
from datetime import date, timedelta

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import CandleError
from scanner_opciones.marketdata.candles import FakeCandles
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, tasty_symbol
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, TODAY, FixedMarket, make_service, seed_market


def bars(closes, end=TODAY - timedelta(days=1)):
    """Una barra por día natural acabando en `end` (los días no importan, solo el orden)."""
    n = len(closes)
    return [(end - timedelta(days=n - 1 - i), c) for i, c in enumerate(closes)]


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
    return Settings.model_validate({"trend": trend})


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


async def test_history_of_all_tickers_is_requested_in_one_call():
    candles = FakeCandles({"AAPL": bars([50.0] * 10 + [90.0] * 20)})
    svc, _ = await service_with(candles)
    await daily(svc)
    assert candles.calls == [["AAPL"]] and svc.history_coverage() == (1, 1)


async def test_provider_failure_keeps_the_stored_history():
    series = bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5, end=TODAY - timedelta(days=3))
    svc, _ = await service_with(FakeCandles({"AAPL": series}))
    await daily(svc)
    svc.daily.candles = FakeCandles(error=CandleError("sin red"))
    svc.watchlist.mark_daily_updated("AAPL", NOW - timedelta(days=1))
    report = await daily(svc)
    assert svc.daily.candles.calls == [["AAPL"]]       # se intentó pedir lo que faltaba
    assert report.errors == {} and sorted(svc.bars.closes("AAPL").items()) == series


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


async def test_up_to_date_history_makes_no_request():
    svc, _ = await service_with(FakeCandles({"AAPL": bars([50.0] * 30)}))     # último cierre: ayer
    await daily(svc)
    candles = await rerun(svc, FakeCandles({"AAPL": []}))
    assert candles.calls == []
    assert len(svc.bars.closes("AAPL")) >= 30                                 # el histórico sigue en la base de datos


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


async def test_history_is_downloaded_even_when_the_daily_update_has_nothing_pending():
    """Regresión: el histórico dependía de que el ticker estuviera pendiente de la actualización diaria de hoy,
    así que tras vaciarlo (migración) los filtros técnicos descartaban todo hasta el día siguiente."""
    candles = FakeCandles({"AAPL": bars([50.0] * 15 + [80.0] * 5 + [95.0] * 5)})
    svc, _ = await service_with(candles)
    await svc.daily.run_pending()                      # la actualización diaria de hoy ya está hecha ...
    assert svc.watchlist.pending_daily_update(NOW.date()) == [] and svc.bars.closes("AAPL") == {}
    assert svc.history_coverage() == (0, 1)
    await svc.run_daily(wait=True)                     # ... y aun así el histórico se completa
    assert svc.history_coverage() == (1, 1) and len(svc.bars.closes("AAPL", official_only=True)) == 25


# ---- cierre provisional del día (último precio del refresco) y arranque ------------------------------------------
def test_provisional_bar_never_overwrites_an_official_one_and_is_replaced_by_it():
    from scanner_opciones.storage.repositories import BarRepo
    repo = BarRepo(Database(":memory:"))
    repo.upsert_provisional("AAPL", TODAY, 100.0)
    repo.upsert_provisional("AAPL", TODAY, 101.5)                      # el último precio visto sustituye al provisional
    assert repo.closes("AAPL") == {TODAY: 101.5} and repo.closes("AAPL", official_only=True) == {}
    assert repo.last_days(["AAPL"]) == {}                               # lo provisional no cuenta como histórico al día
    repo.upsert("AAPL", [(TODAY, 100.9)])                              # llega el oficial
    assert repo.closes("AAPL", official_only=True) == {TODAY: 100.9} and repo.last_days(["AAPL"]) == {"AAPL": TODAY}
    repo.upsert_provisional("AAPL", TODAY, 120.0)                      # ya hay oficial: no se pisa
    assert repo.closes("AAPL") == {TODAY: 100.9}


async def test_refresh_records_the_last_price_of_the_day_in_the_history():
    svc, gw = await service_with(FakeCandles({"AAPL": bars([50.0] * 30)}))
    await svc.daily.run_pending()
    await svc.refresh_all()
    assert svc.bars.closes("AAPL")[TODAY] == 100.0                      # precio del refresco, fecha de la sesión
    gw.prices["AAPL"] = 103.0
    await svc.refresh_all()
    assert svc.bars.closes("AAPL")[TODAY] == 103.0                      # cada refresco actualiza el provisional
    assert svc.bars.closes("AAPL", official_only=True).get(TODAY) is None


async def test_no_provisional_bar_without_a_session_today():
    svc, gw = await service_with(FakeCandles({"AAPL": bars([50.0] * 30)}))
    svc.market = FixedMarket(False)                                     # fin de semana, festivo o antes de la apertura
    await svc.daily.run_pending()
    await svc.refresh_all()
    assert TODAY not in svc.bars.closes("AAPL")


async def test_provisional_bar_is_replaced_by_the_official_close_the_next_day():
    svc, gw = await service_with(FakeCandles({"AAPL": bars([50.0] * 30, end=TODAY - timedelta(days=2))}))
    await daily(svc)
    gw.prices["AAPL"] = 100.0
    await svc.refresh_all()                                              # provisional de hoy = 100
    official = [(TODAY, 99.2)]
    svc.daily.candles = FakeCandles({"AAPL": official})
    svc.now = lambda: NOW + timedelta(days=1)
    svc.daily.now = svc.now
    await svc.daily.update_history(["AAPL"])
    assert svc.bars.closes("AAPL", official_only=True)[TODAY] == 99.2    # oficial; no hace falta recargar todo
    assert svc.daily.candles.days == [3 + 7]                         # desde el último cierre oficial (hace 3 días) + solape


async def test_start_downloads_the_history_before_connecting_or_refreshing():
    candles = FakeCandles({"AAPL": bars([50.0] * 30)})
    svc, gw = await service_with(candles)
    order = []
    original = svc.refresh_all

    async def spy(*a, **kw):
        order.append(("refresh", svc.history_coverage()))
        return await original(*a, **kw)

    svc.refresh_all = spy
    await svc.start()
    await svc.wait_idle()
    assert order and order[0] == ("refresh", (1, 1))                    # al primer refresco el histórico ya estaba completo
