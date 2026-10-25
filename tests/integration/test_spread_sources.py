"""Fuentes aptas para spreads (la del radar de Tortuga y las marcadas a mano) y retirada del antiguo «Universo para spreads»."""
import json
from datetime import datetime

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.storage.db import Database
from scanner_opciones.watchlist.parser import parse_tokens
from tests.integration.test_tortuga import doc, row
from tests.integration.test_web import client_and_service  # noqa: F401

NOW = datetime(2026, 10, 17, 12, 0)


def make(tmp_path):
    settings = Settings.model_validate({"tortuga": {"radar_path": str(tmp_path / "radar.json")}})
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    for t in ("SPY", "AAPL", "MSFT", "MPC", "IBIT"):
        svc._has_options[t] = True
    return svc


async def test_the_tortuga_radar_source_is_always_a_spread_source_and_marked_ones_follow(tmp_path):
    svc = make(tmp_path)
    await svc.add_manual_tickers(parse_tokens(["MPC", "AAPL"]), "TortugaSpreads")
    await svc.add_manual_tickers(parse_tokens(["AAPL", "MSFT"]), "MisCalidad")             # AAPL también está en una fuente normal
    assert svc.spread_only_tickers() == {"MPC"}
    svc.set_spread_source("MisCalidad", True)
    assert svc.spread_only_tickers() == {"MPC", "AAPL", "MSFT"}
    svc.set_spread_source("MisCalidad", False)
    svc.set_spread_source("TortugaSpreads", False)                                           # la del radar no se puede desmarcar
    assert svc.spread_only_tickers() == {"MPC"} and "TortugaSpreads" in svc.spread_source_names()
    with pytest.raises(WatchlistError):
        svc.set_spread_source("NoExiste", True)


async def test_etfs_of_the_radar_are_exempt_from_company_filters(tmp_path):
    svc = make(tmp_path)
    assert "=NEWETF" not in svc.quality_exempt() and "=SPY" in svc.quality_exempt()           # los de config.etf_index_symbols siguen
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("NEWETF", sector="ETF"), row("MPC")])), encoding="utf-8")
    assert "=NEWETF" in svc.quality_exempt() and "=MPC" not in svc.quality_exempt()


async def test_the_old_spread_universe_source_is_removed_once_and_nothing_else_is_touched(tmp_path):
    svc = make(tmp_path)
    await svc.add_manual_tickers(parse_tokens(["SPY", "AAPL"]), "Universo_BPS")
    await svc.add_manual_tickers(parse_tokens(["MSFT"]), "MisCalidad")
    svc.meta.set("spread_universe_synced", "2026-10-16T10:00:00")
    svc.meta.set("spread_sources", json.dumps(["Universo_BPS", "MisCalidad"]))
    assert svc.remove_legacy_spread_universe() is True
    assert "Universo_BPS" not in svc.manual_sources and svc.manual_sources["MisCalidad"] == ["MSFT"]
    assert json.loads(svc.meta.get("spread_sources")) == ["MisCalidad"] and not svc.meta.get("spread_universe_synced")
    assert svc.remove_legacy_spread_universe() is False


def test_universe_page_offers_the_spread_toggle(client_and_service):
    client, svc, *_ = client_and_service
    svc.manual_sources["Manual1"] = ["AAPL"]
    svc.manual_sources["TortugaSpreads"] = ["AAPL"]
    page = client.get("/universe?src=Manual1").text
    assert "Marcar como fuente apta para spreads" in page and "/universe/source/spread" in page
    r = client.post("/universe/source/spread", data={"src": "Manual1", "on": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "Manual1" in svc.spread_source_names()
    assert "Fuente apta para spreads" in client.get("/universe?src=Manual1").text
    assert "no se puede desmarcar" in client.get("/universe?src=TortugaSpreads").text


async def test_nlv_carries_the_euro_rate_and_the_index_is_exempt_without_tastytrade_liquidity(tmp_path):
    from scanner_opciones.domain.models import AccountSummary
    from scanner_opciones.scanner.currency import Nlv, usd_rate
    svc = make(tmp_path)
    svc.state.account = AccountSummary("U1", net_liquidation=40000.0, base_currency="EUR", usd_per_base=1.1)
    nlv = svc.nlv_usd()
    assert isinstance(nlv, Nlv) and abs(float(nlv) - 44000.0) < 1e-6 and usd_rate(nlv, "ESTX50") == 1.1 and usd_rate(nlv, "AAPL") == 1.0
    svc.state.account = AccountSummary("U1", net_liquidation=40000.0, base_currency="USD", usd_per_base=1.0)
    assert usd_rate(svc.nlv_usd(), "ESTX50") == 1.0            # una cuenta en USD no tiene el tipo de cambio del euro: se compara sin convertir
    svc.state.account = None
    assert svc.nlv_usd() is None
    assert {"=ESTX50", "~ESTX50"} <= set(svc.quality_exempt())
