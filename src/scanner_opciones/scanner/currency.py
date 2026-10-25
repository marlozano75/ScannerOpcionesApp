"""Moneda de las opciones. Los filtros en dólares (crédito mínimo, capital en riesgo, pérdida máxima, pesos sobre el NLV) comparan importes de contratos con
umbrales en USD; los instrumentos de TWS que no cotizan en dólares (Euro Stoxx 50 en euros) los convierten con el tipo de cambio de su moneda."""
from __future__ import annotations

from typing import Mapping, Optional


class Nlv(float):
    """NLV de la cuenta en USD que además lleva los dólares por unidad de la moneda de cada ticker cuyas opciones no cotizan en USD. Se usa como un float
    (los filtros que solo miran el NLV no cambian); `usd_rate` da el tipo de cambio de un ticker."""
    rates: Mapping[str, float]

    def __new__(cls, usd: float, rates: Optional[Mapping[str, float]] = None) -> "Nlv":
        obj = super().__new__(cls, usd)
        obj.rates = dict(rates or {})
        return obj


def usd_rate(nlv_usd: Optional[float], ticker: str) -> float:
    """Dólares por unidad de la moneda en que cotizan las opciones de `ticker`: 1 si cotizan en USD o si no se conoce el tipo de cambio (sin NLV en USD
    tampoco hay tipo de cambio: los importes se comparan tal cual)."""
    return nlv_usd.rates.get(ticker, 1.0) if isinstance(nlv_usd, Nlv) else 1.0
