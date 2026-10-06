"""Tendencia alcista a partir de cierres diarios: precio > media corta > media larga."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence


@dataclass(frozen=True)
class TrendStats:
    sma_short: float
    sma_long: float


def sma(values: Sequence[float], n: int) -> Optional[float]:
    """Media simple de los últimos `n` valores; None si no hay suficientes."""
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def compute_trend(
    bars: Sequence[tuple[date, float]], today: date, n_short: int, n_long: int
) -> Optional[TrendStats]:
    """Medias de las barras diarias ya cerradas (se ignora la de hoy, que está en curso).
    None si el histórico no llega para la media larga."""
    closes = [close for day, close in sorted(bars) if day < today and close > 0]
    short, long_ = sma(closes, n_short), sma(closes, n_long)
    if short is None or long_ is None:
        return None
    return TrendStats(round(short, 4), round(long_, 4))


def is_uptrend(price: Optional[float], sma_short: Optional[float], sma_long: Optional[float]) -> Optional[bool]:
    """True si precio > media corta > media larga; None si falta algún dato."""
    if price is None or sma_short is None or sma_long is None:
        return None
    return price > sma_short > sma_long
