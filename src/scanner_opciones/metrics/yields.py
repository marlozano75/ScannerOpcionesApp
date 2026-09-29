"""Rendimientos de prima. Prima = mid (bid+ask)/2; yield = prima / strike."""
from __future__ import annotations

from typing import Optional

from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.metrics.spread import mid_price


def gross_yield_pct(bid: Optional[float], ask: Optional[float], strike: Optional[float]) -> Optional[float]:
    """Yield bruto en % = mid / strike * 100."""
    mid = mid_price(bid, ask)
    if mid is None or strike is None or strike <= 0:
        return None
    return mid / strike * 100


def reference_price(
    bid: Optional[float], ask: Optional[float], mode: PriceReference, spread_pct: float = 25.0
) -> Optional[float]:
    """Precio de venta de referencia: bid, mid, o bid + X % del spread (X entre 0 y 100)."""
    if mid_price(bid, ask) is None:   # cotización no válida
        return None
    if mode is PriceReference.BID:
        return bid
    if mode is PriceReference.MID:
        return (bid + ask) / 2
    x = min(max(spread_pct, 0.0), 100.0) / 100
    return bid + x * (ask - bid)


def gross_yield_ref_pct(
    bid: Optional[float], ask: Optional[float], strike: Optional[float],
    mode: PriceReference, spread_pct: float = 25.0,
) -> Optional[float]:
    """Yield bruto en % = precio de referencia / strike * 100."""
    price = reference_price(bid, ask, mode, spread_pct)
    if price is None or strike is None or strike <= 0:
        return None
    return price / strike * 100


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
