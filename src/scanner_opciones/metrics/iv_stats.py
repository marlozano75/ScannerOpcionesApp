"""IV Rank e IV Percentile a partir del historial de IV del subyacente."""
from __future__ import annotations

import math
from typing import Optional, Sequence


def _clean(history: Sequence[Optional[float]]) -> list[float]:
    return [x for x in history if x is not None and not math.isnan(x)]


def iv_rank(current: Optional[float], history: Sequence[Optional[float]]) -> Optional[float]:
    """(IV - min) / (max - min) * 100 sobre la ventana; acotado a [0, 100].

    None si no hay historial, IV actual o el rango es cero.
    """
    data = _clean(history)
    if current is None or math.isnan(current) or not data:
        return None
    lo, hi = min(data), max(data)
    if hi == lo:
        return None
    return max(0.0, min(100.0, (current - lo) / (hi - lo) * 100))


def iv_percentile(current: Optional[float], history: Sequence[Optional[float]]) -> Optional[float]:
    """% de días de la ventana con IV estrictamente menor que la actual."""
    data = _clean(history)
    if current is None or math.isnan(current) or not data:
        return None
    return sum(1 for x in data if x < current) / len(data) * 100
