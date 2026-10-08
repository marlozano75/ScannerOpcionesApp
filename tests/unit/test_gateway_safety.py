import inspect
import re

from scanner_opciones.broker import ibkr_gateway


def test_gateway_module_imports_and_never_places_orders():
    src = inspect.getsource(ibkr_gateway)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert not re.search(r"\.placeOrder\(", code)
    assert "whatIfOrderAsync" in code


async def test_what_if_order_sets_tif_and_never_places_order():
    from datetime import date
    from types import SimpleNamespace as NS

    from scanner_opciones.broker.ibkr_gateway import IBKRGateway
    from scanner_opciones.config.settings import IbkrSettings
    from scanner_opciones.domain.models import OptionContract

    seen = {}

    class FakeIB:
        def managedAccounts(self):
            return ["U1"]

        def isConnected(self):
            return True

        async def qualifyContractsAsync(self, *contracts):
            for c in contracts:
                c.conId = 123
            return list(contracts)

        async def whatIfOrderAsync(self, contract, order):
            seen["order"] = order
            return NS(initMarginChange="1500.5")

        def placeOrder(self, *a, **k):
            raise AssertionError("La app no debe enviar órdenes")

    gw = IBKRGateway(IbkrSettings())
    gw.ib = FakeIB()
    margin = await gw.what_if_margin(OptionContract("AAPL", date(2026, 10, 30), 250.0), 2)
    assert margin == 1500.5
    assert seen["order"].action == "SELL" and seen["order"].totalQuantity == 2 and seen["order"].tif == "DAY"


async def test_what_if_without_answer_times_out_instead_of_hanging():
    """TWS deja de responder si pierde la conexión con IBKR (error 1100): el what-if no puede colgar el ciclo."""
    import asyncio
    from datetime import date

    import pytest

    from scanner_opciones.broker.ibkr_gateway import IBKRGateway
    from scanner_opciones.config.settings import IbkrSettings
    from scanner_opciones.domain.errors import DataUnavailableError
    from scanner_opciones.domain.models import OptionContract

    class SilentIB:
        def managedAccounts(self):
            return ["U1"]

        def isConnected(self):
            return True

        async def whatIfOrderAsync(self, contract, order):
            await asyncio.Event().wait()      # no responde nunca

    gw = IBKRGateway(IbkrSettings(what_if_timeout_seconds=0.05))
    gw.ib = SilentIB()
    contract = OptionContract("AAPL", date(2026, 10, 30), 250.0, con_id=123)
    with pytest.raises(DataUnavailableError):
        await gw.what_if_margin(contract, 1)


async def test_account_summary_never_opens_more_than_one_request_while_tws_is_silent():
    """ib_async abre una suscripción nueva en cada llamada mientras el resumen no está cargado; TWS acaba
    rechazándolas con el error 322. Con TWS mudo debe haber una sola petición en vuelo."""
    import asyncio
    from types import SimpleNamespace as NS

    import pytest

    from scanner_opciones.broker.ibkr_gateway import IBKRGateway
    from scanner_opciones.config.settings import IbkrSettings

    calls = []
    release = asyncio.Event()

    class SilentIB:
        wrapper = NS(acctSummary={})

        def reqAccountSummaryAsync(self):
            calls.append(1)
            return asyncio.ensure_future(release.wait())      # no termina hasta que TWS responda

    gw = IBKRGateway(IbkrSettings())
    gw.ib = SilentIB()
    for _ in range(3):                                       # tres refrescos seguidos que agotan su tiempo
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(gw._summary_values("U1"), timeout=0.05)
    assert len(calls) == 1
    SilentIB.wrapper.acctSummary[("U1", "NetLiquidation", "", "EUR")] = NS(account="U1", tag="NetLiquidation")
    release.set()
    got = await gw._summary_values("U1")                      # ya cargado: no pide nada más
    assert [v.tag for v in got] == ["NetLiquidation"] and len(calls) == 1
