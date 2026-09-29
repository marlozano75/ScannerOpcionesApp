"""Rendimientos de prima. Prima = mid (bid+ask)/2; yield = prima / strike."""
from __future__ import annotations

from typing import Optional

from scanner_opciones.metrics.spread import mid_price


def gross_yield_pct(bid: Optional[float], ask: Optional[float], strike: Optional[float]) -> Optional[float]:
    """Yield bruto en % = mid / strike * 100."""
    mid = mid_price(bid, ask)
    if mid is None or strike is None or strike <= 0:
        return None
    return mid / strike * 100


def annualized_yield_pct(yield_pct: Optional[float], dte: Optional[int]) -> Optional[float]:
    """Yield anualizado lineal = yield * 365 / DTE."""
    if yield_pct is None or dte is None or dte <= 0:
        return None
    return yield_pct * 365 / dte


def strike_distance_pct(price: Optional[float], strike: Optional[float]) -> Optional[float]:
    """Distancia del strike por debajo del precio, en %: (precio - strike) / precio * 100."""
    if price is None or strike is None or price <= 0:
        return None
    return (price - strike) / price * 100
