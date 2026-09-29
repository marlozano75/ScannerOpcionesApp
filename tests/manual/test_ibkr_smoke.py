"""Smoke test contra TWS/IB Gateway real o simulada. Ejecutar a mano:

    .venv/Scripts/python -m pytest tests/manual -m manual -s

Requiere TWS abierto con la API activada (Configuración > API) y config/config.yaml.
Sirve también para VERIFICAR los nombres de los valores de cuenta (Cushion / HighestSeverity / Post-Expiration).
"""
from pathlib import Path

import pytest

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import load_settings

pytestmark = [pytest.mark.manual, pytest.mark.asyncio]
CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


@pytest.fixture
async def gw():
    ibkr = load_settings(CONFIG).ibkr
    # clientId distinto para no chocar con la app en ejecución
    g = IBKRGateway(ibkr.model_copy(update={"client_id": ibkr.client_id + 100}))
    await g.connect()
    yield g
    await g.disconnect()


async def test_account_tags_dump(gw):
    account = gw.ib.managedAccounts()[0]
    print(f"\nCuenta {account}: valores que contienen 'Excess', 'Cushion', 'Severity' o 'PostExp':")
    for v in gw.ib.accountValues(account):
        if any(k in v.tag for k in ("Excess", "Cushion", "Severity", "PostExp", "NetLiquidation")):
            print(f"  {v.tag:45s} {v.currency:5s} {v.value}")
    summary = await gw.get_account_summary()
    print(summary)
    assert summary.net_liquidation is not None


async def test_chain_and_iv(gw):
    chain = await gw.get_option_chain("AAPL")
    assert chain.expiries and chain.strikes
    iv = await gw.get_iv_history("AAPL", None)
    assert len(iv) > 100


async def test_vix(gw):
    v = await gw.get_vix_data(5, 3)
    print(v)
    assert v.current is not None
