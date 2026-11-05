"""Calls del Call Debit Spread: se guardan y cotizan solo con `scanner.cds.enabled`, y no entran en los escaneos de puts."""
from dataclasses import replace
from datetime import timedelta

from scanner_opciones.config.settings import CdsSettings, Settings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionChain, OptionQuote
from tests.integration.test_jobs import Env, TODAY


def _env(enabled: bool) -> Env:
    env = Env()
    env.settings = env.settings.model_copy(update={"scanner": env.settings.scanner.model_copy(update={"cds": CdsSettings(enabled=enabled, monthly_only=False)})})
    env.daily.settings = env.refresh.settings = env.daily.syncer.settings = env.settings
    env.gw.sectors["ZZZ"] = ("Technology", "Software")
    env.gw.prices["ZZZ"] = 100.0
    env.gw.chains["ZZZ"] = OptionChain("ZZZ", [TODAY + timedelta(days=30), TODAY + timedelta(days=300)], [80.0, 130.0, 140.0])
    env.watch.add(["ZZZ"], env.clock)
    return env


async def test_calls_are_stored_and_quoted_only_when_enabled():
    off = _env(False)
    await off.daily.run_pending()
    assert all(c.right is OptionRight.PUT for c in off.contracts.list())

    on = _env(True)
    await on.daily.run_pending()
    calls = [c for c in on.contracts.list() if c.right is OptionRight.CALL]
    assert {(c.strike, c.expiry) for c in calls} == {(130.0, TODAY + timedelta(days=300)), (140.0, TODAY + timedelta(days=300))}
    for c in calls:
        on.gw.quotes[c] = OptionQuote(bid=3.0, ask=3.2, delta=0.3, iv=0.5, open_interest=50)
    await on.refresh.run()
    quoted = [s for s in on.snaps.all() if s.contract.right is OptionRight.CALL]
    assert len(quoted) == 2 and all(s.initial_margin is None for s in quoted)


def test_cds_page_renders_off_and_on():
    from fastapi.testclient import TestClient
    from scanner_opciones.broker.fake_gateway import FakeGateway
    from scanner_opciones.ui.web import create_app
    from tests.integration.test_service import make_service

    svc, _ = make_service()
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    with TestClient(app) as client:
        client.portal.call(svc.wait_idle)
        page = client.get("/cds")
        assert page.status_code == 200 and "scanner.cds.enabled" in page.text
        svc.settings = svc.settings.model_copy(update={"scanner": svc.settings.scanner.model_copy(update={"cds": CdsSettings(enabled=True, monthly_only=False)})})
        page = client.get("/cds")
        assert page.status_code == 200 and "Ningún spread cumple" in page.text
