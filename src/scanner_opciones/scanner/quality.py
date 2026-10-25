"""Filtros de calidad de la empresa. Funciones puras sobre la ficha del ticker (`TickerInfo`) y el vencimiento.

Un dato que falta cuenta como «no cumple» en los filtros que lo exigen (beneficios, trimestres, liquidez,
solvencia): que se desconozca no es que sea bueno. Los sectores exentos (`scanner.quality.exempt_sectors`:
financiero, energía, utilities, materiales, inmobiliario) pasan sin medirse el apalancamiento, la solvencia y la caja.
Los ETF e índices (`etf_index_symbols`) quedan además exentos de beneficios y trimestres. La excepción es el filtro de resultados: sin fecha conocida no se puede saber si caen antes del vencimiento, y no se
descarta nada. Una fecha pasada (la del último informe) se ignora.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import SOLVENCY_RULES, ScanCriteria


DEFAULT_EXEMPT = ("financ", "energy", "utilit", "material", "real estate")


def is_fund(info: Optional[TickerInfo], exempt_sectors: Sequence[str] = DEFAULT_EXEMPT) -> bool:
    """ETF o índice: aparece en la lista con el prefijo «=» (`scanner.quality.etf_index_symbols`). No tiene EPS ni datos en EDGAR."""
    return info is not None and f"={info.ticker.upper()}" in exempt_sectors


def is_exempt(info: Optional[TickerInfo], exempt_sectors: Sequence[str] = DEFAULT_EXEMPT) -> bool:
    """Sectores con una estructura de deuda y de caja especial (bancos y aseguradoras, energía, utilities, materiales,
    inmobiliarias) y ETF/índices: el apalancamiento, la solvencia y el flujo de caja no se les exigen, pasan sin medirse."""
    if is_fund(info, exempt_sectors):
        return True
    sector = ((info.sector if info else None) or "").lower()
    return any(part in sector for part in exempt_sectors if not part.startswith(("=", "~")))


def exemption(info: Optional[TickerInfo], exempt_sectors: Sequence[str] = DEFAULT_EXEMPT) -> tuple[bool, bool]:
    """(es ETF/índice, está exento) de un ticker: no dependen del filtro, así que quien evalúa muchos filtros sobre el mismo ticker lo calcula una vez."""
    return is_fund(info, exempt_sectors), is_exempt(info, exempt_sectors)


def ticker_quality_reject(
    info: Optional[TickerInfo], criteria: ScanCriteria, exempt_sectors: Sequence[str] = DEFAULT_EXEMPT,
    flags: Optional[tuple[bool, bool]] = None,
) -> Optional[str]:
    """Motivo por el que el ticker no pasa los filtros de calidad de la EMPRESA (todos menos el de resultados, que
    depende del vencimiento de cada contrato), o None. Es lo que usa la vista del Universo."""
    fund, exempt = flags if flags is not None else exemption(info, exempt_sectors)   # un ETF no tiene beneficios ni trimestres: solo se le pide la liquidez
    if criteria.require_profitable and not fund and (info is None or info.eps_ttm is None or info.eps_ttm <= 0):
        return "sin beneficios (EPS 12 m ≤ 0 o sin dato)"
    n = criteria.min_positive_quarters
    if n is not None and not fund and (info is None or info.positive_quarters is None or info.positive_quarters < n):
        return f"menos de {n} de los últimos 4 trimestres con beneficios"
    no_liquidity = info is not None and f"~{info.ticker.upper()}" in exempt_sectors      # instrumento de TWS: tastytrade no da su liquidez (la miden OI, bid size y spread)
    if criteria.min_option_liquidity is not None and not no_liquidity and (
        info is None or info.option_liquidity is None or info.option_liquidity < criteria.min_option_liquidity
    ):
        return f"liquidez de opciones inferior a {criteria.min_option_liquidity}"
    if exempt:
        return None
    if criteria.max_liabilities_to_equity is not None and (
        info is None or info.liabilities_to_equity is None
        or info.liabilities_to_equity > criteria.max_liabilities_to_equity
    ):
        return f"pasivo/patrimonio superior a {criteria.max_liabilities_to_equity:g} (o sin dato / patrimonio negativo)"
    if criteria.require_positive_fcf and (info is None or info.fcf_ttm is None or info.fcf_ttm <= 0):
        return "flujo de caja libre ≤ 0 (o sin dato)"
    if criteria.require_manageable_debt:
        de = info.debt_to_equity if info is not None else None
        cover = info.interest_coverage if info is not None else None
        if not ((de is not None and de <= criteria.manageable_max_de)
                or (cover is not None and cover >= criteria.manageable_min_cover)):
            return (f"deuda alta y mal cubierta (deuda/patrimonio > {criteria.manageable_max_de:g} y cobertura "
                    f"< {criteria.manageable_min_cover:g}×, o sin dato)")
    for field, attr, kind, label in SOLVENCY_RULES:
        limit = getattr(criteria, field)
        if limit is None:
            continue
        value = getattr(info, attr, None) if info is not None else None
        if value is None or (value > limit if kind == "max" else value < limit):
            return f"{label} {'superior' if kind == 'max' else 'inferior'} a {limit:g} (o sin dato)"
    return None


def put_floor_reject(
    info: Optional[TickerInfo], require_profitable: bool, min_positive_quarters: int, require_positive_fcf: bool,
    exempt_sectors: Sequence[str] = DEFAULT_EXEMPT,
) -> Optional[str]:
    """Motivo por el que el ticker no tiene la calidad mínima para vender puts desnudas (beneficios 12 m, trimestres con beneficios y FCF
    positivo), o None. Mismas reglas que el filtro del Universo: un dato que falta no cumple, el ETF no se mide y los sectores exentos
    pasan sin medirse el FCF."""
    fund = is_fund(info, exempt_sectors)
    if fund:
        return None
    if require_profitable and (info is None or info.eps_ttm is None or info.eps_ttm <= 0):
        return "sin beneficios (EPS 12 m ≤ 0 o sin dato)"
    if min_positive_quarters and (info is None or info.positive_quarters is None or info.positive_quarters < min_positive_quarters):
        return f"menos de {min_positive_quarters} de los últimos 4 trimestres con beneficios (o sin dato)"
    if require_positive_fcf and not is_exempt(info, exempt_sectors) and (info is None or info.fcf_ttm is None or info.fcf_ttm <= 0):
        return "flujo de caja libre ≤ 0 (o sin dato)"
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


def spread_universe_reject(
    info: Optional[TickerInfo], min_liquidity: int, price_min: float, price_max: Optional[float] = None,
) -> Optional[str]:
    """Motivo por el que un ticker de una fuente apta para spreads no sirve, o None. No se mide la empresa (en un spread el riesgo está
    definido): solo la liquidez de opciones (sin dato no cumple) y el precio del subyacente (sin precio no se descarta: aún no se ha bajado)."""
    if info is None or info.option_liquidity is None or info.option_liquidity < min_liquidity:
        return f"liquidez de opciones inferior a {min_liquidity} (o sin dato)"
    price = info.underlying_price
    if price is not None and (price < price_min or (price_max is not None and price > price_max)):
        return f"precio {price:g} fuera de {price_min:g}–{price_max:g}" if price_max is not None else f"precio {price:g} inferior a {price_min:g}"
    return None
