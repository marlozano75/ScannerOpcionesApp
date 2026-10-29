"""ETF y valores del radar de Tortuga exentos de los filtros de la empresa."""
import json
from datetime import datetime

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database
from tests.integration.test_tortuga import doc, row

NOW = datetime(2026, 10, 17, 12, 0)


def make(tmp_path):
    settings = Settings.model_validate({"tortuga": {"radar_path": str(tmp_path / "radar.json")}})
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    for t in ("SPY", "AAPL", "MSFT", "MPC", "IBIT"):
        svc._has_options[t] = True
    return svc


async def test_etfs_of_the_radar_are_exempt_from_company_filters(tmp_path):
    svc = make(tmp_path)
    assert "=NEWETF" not in svc.quality_exempt() and "=SPY" in svc.quality_exempt()           # los de config.etf_index_symbols siguen
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("NEWETF", sector="ETF"), row("MPC")])), encoding="utf-8")
    assert "=NEWETF" in svc.quality_exempt() and "=MPC" not in svc.quality_exempt()


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


async def test_nlv_falls_back_to_the_online_rate_when_ibkr_gives_none(tmp_path):
    from scanner_opciones.domain.models import AccountSummary
    from scanner_opciones.marketdata.fx import FakeFx, FxError
    svc = make(tmp_path)
    svc.fx = FakeFx({"EUR": 1.2})
    svc.state.account = AccountSummary("U1", net_liquidation=1000.0, base_currency="EUR")       # IBKR sin tipo de cambio
    assert svc.nlv_usd() is None
    await svc._update_base_fx()
    assert abs(float(svc.nlv_usd()) - 1200.0) < 1e-6 and svc.fx.calls == ["EUR"]
    await svc._update_base_fx()
    assert svc.fx.calls == ["EUR"]                              # dentro del TTL no se vuelve a pedir
    svc.fx = FakeFx(error=FxError("sin red"))
    svc.state.account = AccountSummary("U1", net_liquidation=1000.0, base_currency="EUR", usd_per_base=1.1)
    await svc._update_base_fx()
    assert svc.fx.calls == [] and abs(float(svc.nlv_usd()) - 1100.0) < 1e-6      # el de IBKR manda y no se pide nada
