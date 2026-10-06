"""Filtros técnicos del scanner: tendencia, medias, zona de soporte y días desde el último toque del strike."""
from datetime import timedelta

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import OptionQuote
from scanner_opciones.storage.db import Database
from tests.integration.test_service import NOW, TODAY, FixedMarket, make_service, seed_market


def bars(values, end=TODAY - timedelta(days=1)):
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), float(v)) for i, v in enumerate(values)]


async def service_with_history(values):
    """AAPL a 100 con contratos put de strike 75 y 80 (DTE 30) cotizados, y el histórico `values` guardado."""
    _, gw = make_service()
    seed_market(gw)
    svc = AppService(gw, Database(":memory:"), Settings(), lambda: NOW, market=FixedMarket(True))
    await gw.connect()
    svc.watchlist.add(["AAPL"], NOW)
    await svc.daily.run_pending()
    for c in svc.contracts.list():
        gw.quotes[c] = OptionQuote(bid=1.0, ask=1.2, open_interest=500)
    await svc.refresh_all()
    if values is not None:
        svc.bars.upsert("AAPL", bars(values))
    return svc


def strikes(svc, **overrides):
    crit = svc.criteria().with_filters(strike_below_pct_min=1, strike_below_pct_max=40, min_annual_yield_pct=0,
                                       dte_min=1, dte_max=45, **overrides)
    return sorted(r.snapshot.contract.strike for r in svc.scan(crit).results)


async def test_without_technical_filters_the_history_is_not_needed():
    svc = await service_with_history(None)
    assert strikes(svc) == [75.0, 80.0]


async def test_ticker_without_history_is_rejected_by_any_technical_filter():
    svc = await service_with_history(None)
    assert strikes(svc, ma50="above") == []
    out = svc.scan(svc.criteria().with_filters(strike_below_pct_min=1, min_annual_yield_pct=0, dte_max=45, ma50="above"),
                   include_rejections=True)
    assert any("sin histórico" in why for why in out.rejections.values())


@pytest.mark.parametrize("line, values, side, expected", [
    ("ma50", [90] * 60, "above", [75.0, 80.0]),      # precio 100 > media 90
    ("ma50", [90] * 60, "below", []),
    ("ma50", [110] * 60, "below", [75.0, 80.0]),     # precio 100 < media 110
    ("ma200", [90] * 60, "above", []),               # 60 cierres no bastan para la media de 200
    ("ema9", [90] * 20, "above", [75.0, 80.0]),
    ("ema20", [110] * 25, "above", []),
])
async def test_moving_average_filters(line, values, side, expected):
    svc = await service_with_history(values)
    assert strikes(svc, **{line: side}) == expected


async def test_uptrend_and_downtrend_with_unbroken_low():
    rising = [60 + i for i in range(40)] + [100] * 5                    # mínimo 60 hace 45 días, +67 %
    svc = await service_with_history(rising)
    assert strikes(svc, trend_direction="up", trend_min_days=30) == [75.0, 80.0]
    assert strikes(svc, trend_direction="up", trend_min_days=90) == []   # el mínimo no es tan antiguo
    assert strikes(svc, trend_direction="down") == []
    falling = [140 - i for i in range(40)] + [100] * 5
    svc = await service_with_history(falling)
    assert strikes(svc, trend_direction="down", trend_min_days=30) == [75.0, 80.0]
    assert strikes(svc, trend_direction="up") == []


async def test_uptrend_with_rising_highs_and_lows_in_the_weekly_window():
    # un cierre por semana (viernes), ciclos de 5 semanas (sube, máximo, retroceso, mínimo) que cada vez acaban más alto
    last_friday = TODAY - timedelta(days=(TODAY.weekday() - 4) % 7 or 7)
    weekly = [(last_friday - timedelta(weeks=29 - w), float(40 + 8 * (w // 5) + [0, 5, 10, 6, 3][w % 5])) for w in range(30)]
    svc = await service_with_history(None)
    svc.bars.upsert("AAPL", weekly)
    crit = dict(trend_direction="up", trend_method="swings", trend_frame="weekly")
    assert strikes(svc, **crit) == [75.0, 80.0]
    assert strikes(svc, **{**crit, "trend_direction": "down"}) == []
    svc.bars.upsert("AAPL", [(d, 200.0 - px) for d, px in weekly])               # el espejo: máximos y mínimos decrecientes
    assert strikes(svc, **{**crit, "trend_direction": "up"}) == []


async def test_support_zone_requires_the_strike_to_be_at_or_below_it():
    def bounce(low):                                                      # caída a `low` y vuelta a 100, con 25 días de calma
        return [95, 90, 85, 80, low, 82, 88, 94] + [100] * 25

    svc = await service_with_history([100] * 5 + bounce(78) + bounce(78.5) + bounce(78.2) + [100, 100])
    assert strikes(svc, require_support=True) == [75.0]                   # 75 ≤ soporte (78,5); 80 queda por encima
    svc = await service_with_history([100] * 40)
    assert strikes(svc, require_support=True) == []                       # sin zona probada


async def test_min_days_since_the_strike_was_last_visited():
    history = [100] * 40 + [79.0] + [100] * 9                          # un cierre en 79 hace 10 días: visitó el strike 80
    svc = await service_with_history(history)
    assert strikes(svc, min_days_since_touch=5) == [75.0, 80.0]
    assert strikes(svc, min_days_since_touch=30) == [75.0]              # el 80 se visitó hace menos de 30 días
    assert strikes(svc, min_days_since_touch=None) == [75.0, 80.0]
