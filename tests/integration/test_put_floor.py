"""Calidad mínima de las puts desnudas (beneficios 12 m, 3 trimestres, FCF) y tendencia alcista a largo plazo de lo que solo puede ir como spread."""
from datetime import date, timedelta

import pytest

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.technical import long_term_uptrend
from scanner_opciones.scanner.quality import put_floor_reject
from tests.integration.test_service import TODAY, started_service

pytestmark = pytest.mark.put_floor
GOOD = TickerInfo("AAPL", eps_ttm=5.0, positive_quarters=4, fcf_ttm=1e9)


def test_floor_needs_profit_quarters_and_free_cash_flow():
    ok = lambda info, **kw: put_floor_reject(info, True, 3, True, **kw)           # noqa: E731
    assert ok(GOOD) is None
    assert "sin beneficios" in ok(TickerInfo("X", eps_ttm=-1, positive_quarters=4, fcf_ttm=1e9))
    assert "trimestres" in ok(TickerInfo("X", eps_ttm=1, positive_quarters=2, fcf_ttm=1e9))
    assert ok(TickerInfo("X", eps_ttm=1, positive_quarters=3, fcf_ttm=1e9)) is None          # 3 de 4 basta
    assert "flujo de caja" in ok(TickerInfo("X", eps_ttm=1, positive_quarters=4, fcf_ttm=-5))
    assert ok(None) is not None and ok(TickerInfo("X")) is not None                           # un dato que falta no cumple
    assert put_floor_reject(TickerInfo("X", eps_ttm=1, positive_quarters=4), True, 3, False) is None    # sin EDGAR no se exige el FCF
    bank = TickerInfo("JPM", sector="Financial", eps_ttm=1, positive_quarters=4)
    assert put_floor_reject(bank, True, 3, True, ("financ",)) is None                         # sector exento: no se mide el FCF
    assert put_floor_reject(TickerInfo("SPY"), True, 3, True, ("=SPY",)) is None               # ETF: no se mide nada


def test_long_term_uptrend_needs_price_over_a_rising_average():
    rising = [float(i) for i in range(1, 260)]
    assert long_term_uptrend(rising, 200, 20)[0] is True
    assert long_term_uptrend(rising[:150], 200, 20)[0] is None                                # sin histórico
    ok, why = long_term_uptrend(rising[:150], 200, 20, fallback_n=100)
    assert ok is True and "media de 100 en vez de 200" in why                                 # histórico corto: respaldo con la SMA100
    assert long_term_uptrend(rising[:120], 200, 20, fallback_n=100)[0] is True
    assert long_term_uptrend(rising[:119], 200, 20, fallback_n=100)[0] is None                # hacen falta 100 sesiones más las 20 de la pendiente
    assert long_term_uptrend(rising[::-1][:150], 200, 20, fallback_n=100)[0] is False
    falling = rising[::-1]
    ok, why = long_term_uptrend(falling, 200, 20)
    assert ok is False and "por debajo" in why
    bounce = falling[:240] + [falling[239] + 90.0] * 19 + [falling[239] + 91.0]               # sobre la media, pero la media aún baja
    ok, why = long_term_uptrend(bounce, 200, 20)
    assert ok is False and "no sube" in why


def bars(values):
    start = TODAY - timedelta(days=len(values))
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


async def test_a_ticker_without_the_floor_is_only_scanned_as_a_spread_with_an_uptrend():
    svc, gw = await started_service()
    object.__setattr__(svc.settings, "spread_universe", svc.settings.spread_universe.model_copy(update={"require_uptrend": True}))   # por defecto está apagado
    crit = svc.criteria().with_filters(strike_below_pct_min=1, strike_below_pct_max=40, min_yield_pct=0, dte_min=1, dte_max=45)
    assert svc.scan(crit).results == []                                  # sin datos de calidad no cumple: ninguna put desnuda
    assert "AAPL" in svc.put_floor_reasons() and "calidad mínima" in svc.scan(crit, include_rejections=True).rejections.popitem()[1]
    assert "1 de 1 tickers no tienen la calidad mínima" in svc.put_floor_notice()

    svc.quality.save([GOOD])                                             # con calidad vuelve a salir
    svc._scan_memo.clear()
    assert len(svc.scan(crit).results) == 2 and svc.put_floor_reasons() == {} and svc.put_floor_notice() is None

    svc.quality.save([TickerInfo("AAPL", eps_ttm=-1, positive_quarters=4, fcf_ttm=1e9)])
    svc.bars.upsert("AAPL", bars([float(50 + i * 0.3) for i in range(260)]))            # sin calidad, pero tendencia alcista
    svc._scan_memo.clear()
    assert svc.scan(crit).results == [] and svc.no_spread_reasons() == {}
    spread = svc.scan(crit.with_filters(spread_mode=True))
    assert spread.results                                                # como spread, la vendida sí se escanea
    assert svc.watchlist_modes()["AAPL"]["mode"] == "Solo spread"

    svc.bars.upsert("AAPL", bars([float(130 - i * 0.3) for i in range(260)]))           # y en tendencia bajista, ni como spread
    svc._scan_memo.clear()
    assert svc.scan(crit.with_filters(spread_mode=True)).results == []
    assert "sin tendencia alcista" in svc.no_spread_reasons()["AAPL"] and svc.watchlist_modes()["AAPL"]["mode"] == "Ninguno"


async def test_the_floor_can_be_switched_off_and_the_watchlist_shows_the_modes():
    from fastapi.testclient import TestClient
    from scanner_opciones.ui.web import create_app
    svc, gw = await started_service()
    page = TestClient(create_app(svc)).get("/watchlist").text
    assert "Calidad puts" in page and "Solo spread" in page or "Ninguno" in page
    object.__setattr__(svc.settings.scanner.quality, "put_floor", svc.settings.scanner.quality.put_floor.model_copy(update={"enabled": False}))
    svc._scan_memo.clear()
    assert svc.put_floor_reasons() == {}
