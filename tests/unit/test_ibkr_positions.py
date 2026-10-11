"""Posiciones vía IBKRGateway: `ib.portfolio()` solo se llena tras suscribir la cuenta con reqAccountUpdates."""
from datetime import datetime
from types import SimpleNamespace as NS

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import Accounts, IbkrSettings


class FakeIB:
    """Con dos cuentas gestionadas no hay ninguna suscrita hasta pedirlo (comportamiento verificado contra TWS)."""

    def __init__(self):
        self.subscribed = None
        self.requests = []

    def isConnected(self):
        return True

    def managedAccounts(self):
        return ["U1", "U2"]

    async def reqAccountUpdatesAsync(self, account):
        self.requests.append(account)
        self.subscribed = account

    def portfolio(self, account):
        if self.subscribed != account:
            return []
        stock = NS(symbol="AAPL", secType="STK")
        return [NS(contract=stock, position=10.0, marketValue=1500.0)]

    def sectors(self):
        return {}


async def test_positions_subscribe_the_configured_account_once():
    gw = IBKRGateway(IbkrSettings(accounts=Accounts(paper="U2")), now=lambda: datetime(2026, 9, 29, 10))
    gw.ib = FakeIB()
    gw._sector_cache["AAPL"] = ("Technology", "Electronics")
    first = await gw.get_positions()
    assert [(p.ticker, p.quantity, p.sector) for p in first] == [("AAPL", 10.0, "Technology")]
    await gw.get_positions()
    assert gw.ib.requests == ["U2"]          # la suscripción se pide una vez, no en cada lectura


def test_account_alias_comes_from_account_or_group():
    from types import SimpleNamespace as NS
    from scanner_opciones.broker.ibkr_mapper import build_account_summary
    from scanner_opciones.config.settings import AccountTags
    values = [NS(tag="AccountOrGroup", value="MiguelAngel", currency="BASE", account="U1"),
              NS(tag="AccountOrGroup", value="Otra", currency="BASE", account="U2"),
              NS(tag="NetLiquidation", value="1000", currency="EUR", account="U1")]
    assert build_account_summary(values, AccountTags(), "U1", datetime(2026, 10, 11)).alias == "MiguelAngel"
