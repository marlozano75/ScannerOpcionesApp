"""Divergencia alcista del MACD(12,26,9) por ticker (2026-11-08). Solo informa; solo se enseña con el mercado en capitulación.

Definición de la investigación (`scripts/suelos/bt3.py`, `cap_ticker.py`): el mínimo del día es igual o menor que el de las 20 sesiones anteriores,
el MACD (EMA12 − EMA26 del cierre) está por encima de su propio mínimo de esas 20 sesiones y es negativo. Función pura sobre las velas diarias.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

FAST, SLOW, LOOKBACK = 12, 26, 20
MIN_BARS = 90                 # el MACD necesita ~3 veces la EMA lenta para estabilizarse
MAX_STALE_DAYS = 5


def macd_divergence(candles: Sequence[tuple], today: date) -> Optional[bool]:
    """candles = (día, apertura, máximo, mínimo, cierre) ascendentes. None sin histórico suficiente o desactualizado."""
    bars = [c for c in candles if c[3] is not None]
    if len(bars) < MIN_BARS or (today - bars[-1][0]).days > MAX_STALE_DAYS:
        return None
    closes = [c[4] for c in bars]
    kf, ks = 2 / (FAST + 1), 2 / (SLOW + 1)
    ef = es = closes[0]
    macd = [0.0]
    for px in closes[1:]:
        ef += kf * (px - ef)
        es += ks * (px - es)
        macd.append(ef - es)
    lows = [c[3] for c in bars]
    return lows[-1] <= min(lows[-LOOKBACK - 1:-1]) and macd[-1] > min(macd[-LOOKBACK - 1:-1]) and macd[-1] < 0
