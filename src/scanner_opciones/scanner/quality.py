"""Filtros de calidad de la empresa. Funciones puras sobre la ficha del ticker (`TickerInfo`) y el vencimiento.

Un dato que falta cuenta como «no cumple» en los filtros que lo exigen (beneficios, trimestres, capitalización,
liquidez): que se desconozca no es que sea bueno. La excepción es el filtro de resultados: sin fecha conocida no se
puede saber si caen antes del vencimiento, y no se descarta nada. Una fecha pasada (la del último informe) se ignora.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import ScanCriteria


def quality_reject(info: Optional[TickerInfo], expiry: date, criteria: ScanCriteria, today: date) -> Optional[str]:
    """Motivo por el que el contrato no pasa los filtros de calidad, o None."""
    if criteria.require_profitable and (info is None or info.eps_ttm is None or info.eps_ttm <= 0):
        return "sin beneficios (EPS 12 m ≤ 0 o sin dato)"
    n = criteria.min_positive_quarters
    if n is not None and (info is None or info.positive_quarters is None or info.positive_quarters < n):
        return f"menos de {n} de los últimos 4 trimestres con beneficios"
    if criteria.min_market_cap_m is not None and (
        info is None or info.market_cap is None or info.market_cap < criteria.min_market_cap_m * 1e6
    ):
        return f"capitalización inferior a {criteria.min_market_cap_m:g} M$"
    if criteria.min_option_liquidity is not None and (
        info is None or info.option_liquidity is None or info.option_liquidity < criteria.min_option_liquidity
    ):
        return f"liquidez de opciones inferior a {criteria.min_option_liquidity}"
    if criteria.avoid_earnings and info is not None and info.next_earnings is not None:
        if today <= info.next_earnings <= expiry:
            return f"resultados el {info.next_earnings} antes del vencimiento"
    return None
