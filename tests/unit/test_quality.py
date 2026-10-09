"""Filtros de calidad: lógica pura, limpieza del EPS y lectura de los datos de tastytrade."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.fundamentals import Fundamentals, clean_eps, resolve_eps
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.quality import quality_reject

TODAY = date(2026, 10, 8)
EXPIRY = TODAY + timedelta(days=20)
BASE = criteria_from_settings(Settings())


def info(**kw):
    return TickerInfo("AAPL", **kw)


def test_default_criteria_filter_nothing():
    assert not BASE.quality_active
    assert quality_reject(None, EXPIRY, BASE, TODAY) is None


@pytest.mark.parametrize("eps,ok", [(1.5, True), (0.01, True), (0.0, False), (-0.4, False), (None, False)])
def test_profitability_needs_positive_eps(eps, ok):
    crit = BASE.with_filters(require_profitable=True)
    assert (quality_reject(info(eps_ttm=eps), EXPIRY, crit, TODAY) is None) is ok


def test_unknown_ticker_fails_the_filters_that_need_data():
    for kw in ({"require_profitable": True}, {"min_positive_quarters": 3},
               {"min_option_liquidity": 2}):
        assert quality_reject(None, EXPIRY, BASE.with_filters(**kw), TODAY) is not None


def test_consistency_counts_positive_quarters():
    crit = BASE.with_filters(min_positive_quarters=3)
    assert quality_reject(info(positive_quarters=3, reported_quarters=4), EXPIRY, crit, TODAY) is None
    assert quality_reject(info(positive_quarters=2, reported_quarters=4), EXPIRY, crit, TODAY) is not None
    assert quality_reject(info(positive_quarters=None), EXPIRY, crit, TODAY) is not None


def test_liquidity_is_a_rating_and_market_cap_does_not_filter():
    crit = BASE.with_filters(min_option_liquidity=3)
    assert quality_reject(info(market_cap=1e8, option_liquidity=3), EXPIRY, crit, TODAY) is None   # una empresa pequeña pasa
    assert "liquidez" in quality_reject(info(market_cap=5e9, option_liquidity=2), EXPIRY, crit, TODAY)


def test_earnings_filter_works_per_contract():
    crit = BASE.with_filters(avoid_earnings=True)
    t = info(next_earnings=TODAY + timedelta(days=10))
    assert quality_reject(t, TODAY + timedelta(days=5), crit, TODAY) is None          # vence antes de los resultados
    assert quality_reject(t, TODAY + timedelta(days=10), crit, TODAY) is not None     # el mismo día: se descarta
    assert "resultados" in quality_reject(t, TODAY + timedelta(days=20), crit, TODAY)  # los atraviesa


def test_earnings_filter_ignores_unknown_and_past_dates():
    crit = BASE.with_filters(avoid_earnings=True)
    assert quality_reject(info(), EXPIRY, crit, TODAY) is None                                   # sin fecha: no se descarta
    assert quality_reject(None, EXPIRY, crit, TODAY) is None
    assert quality_reject(info(next_earnings=TODAY - timedelta(days=30)), EXPIRY, crit, TODAY) is None   # el último informe


def test_clean_eps_drops_the_values_that_mean_no_data():
    assert clean_eps(-99999.99) is None and clean_eps(0.0) is None and clean_eps(None) is None
    assert clean_eps(-0.43) == -0.43 and clean_eps(8.69) == 8.69          # una pérdida real se conserva


def test_resolve_eps_falls_back_to_the_last_four_quarters():
    assert resolve_eps(8.69, [1, 1, 1, 1]) == 8.69                         # manda el del proveedor si es válido
    assert resolve_eps(-99999.99, [1.9, 2.04, 2.26, 2.47]) == pytest.approx(8.67)   # BNY
    assert resolve_eps(0.0, [7.05, 8.1, 5.94, 7.37]) == pytest.approx(28.46)        # CB
    assert resolve_eps(None, [1.0, 2.0]) is None                           # menos de 4 trimestres: no se inventa


# ---- proveedor real con las llamadas al SDK sustituidas ---------------------------------------------------
def item(symbol, eps, cap=1e10, liq=3, report=None, actual=None, consensus=None):
    earnings = NS(expected_report_date=report, actual_eps=actual, consensus_estimate=consensus) if report else None
    return NS(symbol=symbol, earnings_per_share=eps, market_cap=cap, liquidity_rating=liq, earnings=earnings)


async def test_provider_reads_fundamentals_and_cleans_the_eps():
    async def fetch(symbols):
        return [item("AAPL", 8.69, 4.8e12, 4, date(2026, 10, 29), 1.85, 1.98),
                item("BNY", -99999.99), item("PBR/A", -0.4, liq=None)]

    out = await TastytradeVolatility("s", "t", fetch=fetch).get_fundamentals(["AAPL", "BNY", "PBR-A"])
    assert out["AAPL"] == Fundamentals(8.69, 4.8e12, 4, date(2026, 10, 29), -6.6)   # (1,85 − 1,98) / 1,98
    assert out["BNY"].eps_ttm is None                                              # centinela fuera
    assert out["PBR-A"].eps_ttm == -0.4 and out["PBR-A"].next_earnings is None     # el ticker vuelve con el nombre de la app


async def test_provider_failure_is_a_volatility_error():
    async def boom(symbols):
        raise RuntimeError("sin red")

    with pytest.raises(VolatilityError):
        await TastytradeVolatility("s", "t", fetch=boom).get_fundamentals(["AAPL"])


async def test_quarterly_eps_keeps_the_last_four_and_skips_failures():
    async def earnings(symbol):
        if symbol == "BAD":
            raise RuntimeError("sin datos")
        base = date(2025, 1, 1)
        return [(base + timedelta(days=90 * i), float(i)) for i in range(6)]      # 6 trimestres: 0..5

    out = await TastytradeVolatility("s", "t", fetch_earnings=earnings).get_quarterly_eps(["AAPL", "BAD"])
    assert out == {"AAPL": [2.0, 3.0, 4.0, 5.0]}


async def test_quarterly_eps_uses_tastytrade_symbols():
    seen = []

    async def earnings(symbol):
        seen.append(symbol)
        return []

    assert await TastytradeVolatility("s", "t", fetch_earnings=earnings).get_quarterly_eps(["PBR-A"]) == {}
    assert seen == ["PBR/A"]
