"""Smoke test contra la API real de tastytrade (solo lectura). Ejecutar a mano:

    .venv/Scripts/python -m pytest tests/manual/test_tastytrade_smoke.py -m manual -s

Requiere tastytrade.client_secret y tastytrade.refresh_token en config/config.yaml.
"""
from pathlib import Path

import pytest

from scanner_opciones.config.settings import load_settings
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility

pytestmark = [pytest.mark.manual, pytest.mark.asyncio]
CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


async def test_iv_metrics_for_known_and_unknown_tickers():
    tt = load_settings(CONFIG).tastytrade
    out = await TastytradeVolatility(tt.client_secret, tt.refresh_token).get_iv_metrics(["AAPL", "XXXXNOPE"])
    print(out)
    assert "XXXXNOPE" not in out
    m = out["AAPL"]
    assert 0 <= m.iv_rank <= 100 and 0 <= m.iv_percentile <= 100


async def test_prices_for_known_and_unknown_tickers():
    tt = load_settings(CONFIG).tastytrade
    out = await TastytradeVolatility(tt.client_secret, tt.refresh_token).get_prices(["AAPL", "CBNK", "XXXXNOPE"])
    print(out)
    assert "XXXXNOPE" not in out and out["AAPL"] > 0 and out["CBNK"] > 0


async def test_daily_closes_for_known_and_class_share_tickers():
    from datetime import date

    from scanner_opciones.metrics.trend import compute_trend

    tt = load_settings(CONFIG).tastytrade
    out = await TastytradeVolatility(tt.client_secret, tt.refresh_token).get_daily_closes(["AAPL", "PBR-A", "XXXXNOPE"], 400)
    print({t: (len(b), compute_trend(b, date.today(), 50, 200)) for t, b in out.items()})
    assert "XXXXNOPE" not in out and len(out["AAPL"]) >= 200 and "PBR-A" in out
