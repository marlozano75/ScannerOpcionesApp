"""Filtros de apalancamiento y flujo de caja (fase 2 de la calidad)."""
import pytest

from tests.unit.test_quality import BASE, EXPIRY, TODAY, info
from scanner_opciones.scanner.quality import quality_reject


def test_leverage_filter_needs_data_and_a_ratio_under_the_limit():
    crit = BASE.with_filters(max_liabilities_to_equity=2.0)
    assert crit.quality_active
    assert quality_reject(info(sector="Technology", liabilities_to_equity=1.5), EXPIRY, crit, TODAY) is None
    assert quality_reject(info(sector="Technology", liabilities_to_equity=2.0), EXPIRY, crit, TODAY) is None   # el límite entra
    assert "pasivo/patrimonio" in quality_reject(info(sector="Technology", liabilities_to_equity=2.1), EXPIRY, crit, TODAY)
    assert quality_reject(info(sector="Technology"), EXPIRY, crit, TODAY) is not None      # sin dato (ADR, patrimonio negativo)
    assert quality_reject(None, EXPIRY, crit, TODAY) is not None


def test_free_cash_flow_filter():
    crit = BASE.with_filters(require_positive_fcf=True)
    assert crit.quality_active
    assert quality_reject(info(sector="Technology", fcf_ttm=1e6), EXPIRY, crit, TODAY) is None
    assert "flujo de caja" in quality_reject(info(sector="Technology", fcf_ttm=-5.0), EXPIRY, crit, TODAY)
    assert quality_reject(info(sector="Technology", fcf_ttm=0.0), EXPIRY, crit, TODAY) is not None
    assert quality_reject(info(sector="Technology"), EXPIRY, crit, TODAY) is not None


@pytest.mark.parametrize("sector", ["Financial", "financial services", "Financials", "Energy", "Utilities", "Basic Materials", "Real Estate"])
def test_exempt_sectors_skip_leverage_and_cash_flow(sector):
    """Un banco tiene un pasivo/patrimonio de 10 por naturaleza y no tiene «flujo de caja libre»; energía, utilities,
    materiales e inmobiliario tienen una deuda y una caja especiales."""
    crit = BASE.with_filters(max_liabilities_to_equity=2.0, require_positive_fcf=True)
    assert quality_reject(info(sector=sector, liabilities_to_equity=10.7, fcf_ttm=None), EXPIRY, crit, TODAY) is None
    assert quality_reject(info(sector=sector), EXPIRY, crit, TODAY) is None


def test_the_exemption_does_not_cover_the_other_quality_filters():
    crit = BASE.with_filters(max_liabilities_to_equity=2.0, require_profitable=True)
    assert quality_reject(info(sector="Financial", eps_ttm=-1.0), EXPIRY, crit, TODAY) is not None
