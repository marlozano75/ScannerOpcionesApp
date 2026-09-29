"""Spread bid/ask. Funciones puras: devuelven None si el dato no es computable."""
from __future__ import annotations

import math
from typing import Optional


def _valid(x: Optional[float]) -> bool:
    return x is not None and not math.isnan(x)


def mid_price(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Media de bid y ask. IBKR usa -1 para 'sin dato': se considera inválido."""
    if not (_valid(bid) and _valid(ask)):
        return None
    if bid < 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """(ask - bid) / mid * 100."""
    mid = mid_price(bid, ask)
    if mid is None or mid <= 0:
        return None
    return (ask - bid) / mid * 100
