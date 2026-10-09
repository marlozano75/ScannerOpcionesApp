"""Filtros de calidad de la empresa. Funciones puras sobre la ficha del ticker (`TickerInfo`) y el vencimiento.

Un dato que falta cuenta como «no cumple» en los filtros que lo exigen (beneficios, trimestres, liquidez,
solvencia): que se desconozca no es que sea bueno. Los sectores exentos (`scanner.quality.exempt_sectors`:
financiero, energía, utilities, materiales, inmobiliario) pasan sin medirse el apalancamiento, la solvencia y la caja.
La excepción es el filtro de resultados: sin fecha conocida no se puede saber si caen antes del vencimiento, y no se
descarta nada. Una fecha pasada (la del último informe) se ignora.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import SOLVENCY_RULES, ScanCriteria


DEFAULT_EXEMPT = ("financ", "energy", "utilit", "material", "real estate")


def is_exempt(info: Optional[TickerInfo], exempt_sectors: Sequence[str] = DEFAULT_EXEMPT) -> bool:
    """Sectores con una estructura de deuda y de caja especial (bancos y aseguradoras, energía, utilities, materiales,
    inmobiliarias): el apalancamiento, la solvencia y el flujo de caja no se les exigen, pasan sin medirse."""
    sector = ((info.sector if info else None) or "").lower()
    return any(part in sector for part in exempt_sectors)


def ticker_quality_reject(
    info: Optional[TickerInfo], criteria: ScanCriteria, exempt_sectors: Sequence[str] = DEFAULT_EXEMPT,
) -> Optional[str]:
    """Motivo por el que el ticker no pasa los filtros de calidad de la EMPRESA (todos menos el de resultados, que
    depende del vencimiento de cada contrato), o None. Es lo que usa la vista del Universo."""
    if criteria.require_profitable and (info is None or info.eps_ttm is None or info.eps_ttm <= 0):
        return "sin beneficios (EPS 12 m ≤ 0 o sin dato)"
    n = criteria.min_positive_quarters
    if n is not None and (info is None or info.positive_quarters is None or info.positive_quarters < n):
        return f"menos de {n} de los últimos 4 trimestres con beneficios"
    if criteria.min_option_liquidity is not None and (
        info is None or info.option_liquidity is None or info.option_liquidity < criteria.min_option_liquidity
    ):
        return f"liquidez de opciones inferior a {criteria.min_option_liquidity}"
    if is_exempt(info, exempt_sectors):
        return None
    if criteria.max_liabilities_to_equity is not None and (
        info is None or info.liabilities_to_equity is None
        or info.liabilities_to_equity > criteria.max_liabilities_to_equity
    ):
        return f"pasivo/patrimonio superior a {criteria.max_liabilities_to_equity:g} (o sin dato / patrimonio negativo)"
    if criteria.require_positive_fcf and (info is None or info.fcf_ttm is None or info.fcf_ttm <= 0):
        return "flujo de caja libre ≤ 0 (o sin dato)"
    for field, attr, kind, label in SOLVENCY_RULES:
        limit = getattr(criteria, field)
        if limit is None:
            continue
        value = getattr(info, attr, None) if info is not None else None
        if value is None or (value > limit if kind == "max" else value < limit):
            return f"{label} {'superior' if kind == 'max' else 'inferior'} a {limit:g} (o sin dato)"
    return None


def quality_reject(
    info: Optional[TickerInfo], expiry: date, criteria: ScanCriteria, today: date,
    exempt_sectors: Sequence[str] = DEFAULT_EXEMPT,
) -> Optional[str]:
    """Motivo por el que el contrato no pasa los filtros de calidad, o None."""
    if (why := ticker_quality_reject(info, criteria, exempt_sectors)) is not None:
        return why
    if criteria.avoid_earnings and info is not None and info.next_earnings is not None:
        if today <= info.next_earnings <= expiry:
            return f"resultados el {info.next_earnings} antes del vencimiento"
    return None
