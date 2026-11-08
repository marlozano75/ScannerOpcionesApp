"""Caída reciente de un ticker frente a lo habitual en él (columnas «Caída ATR», «Caída pctl» y «Caída/IV» del Scanner; informativo, no filtra).

Caída = distancia del precio actual al máximo de las últimas `window_days` sesiones, en %. Se compara con tres escalas propias del ticker:
su ATR, su historial de caídas de la misma ventana y el movimiento que descuenta su volatilidad implícita. Funciones puras sobre velas."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.config.settings import DropSettings
from scanner_opciones.metrics import technical as ta

SESSIONS_PER_YEAR = 252


@dataclass(frozen=True)
class DropStats:
    drop_pct: float                        # % que el precio está por debajo del máximo de la ventana
    atr_mult: Optional[float]              # caída en $ / ATR (múltiplos de ATR)
    percentile: Optional[float]            # % de las ventanas del último año con una caída menor o igual
    iv_mult: Optional[float]               # caída % / movimiento esperado por la IV30 en la ventana


def drop_stats(candles: Sequence[tuple], price: Optional[float], iv30_pct: Optional[float], cfg: DropSettings) -> Optional[DropStats]:
    """Estadísticas de caída con velas (día, apertura, máximo, mínimo, cierre) ascendentes. `price` = precio actual (None: último cierre);
    `iv30_pct` = IV a 30 días en %. None si no hay velas suficientes para la ventana y el ATR."""
    n = cfg.window_days
    if len(candles) < max(n, cfg.atr_days + 1):
        return None
    highs = [c[2] for c in candles]
    closes = [c[4] for c in candles]
    px = price if price and price > 0 else closes[-1]
    top = max(highs[-n:])
    if top <= 0 or px >= top:
        top = max(top, px)
    drop = (top - px) / top * 100
    bars = [(c[4], c[2], c[3]) for c in candles[-(cfg.atr_days * 6):]]
    atr = ta.atr(bars, cfg.atr_days)
    atr_mult = (top - px) / atr if atr and atr > 0 else None
    # historial: caída al cierre de cada sesión de los últimos `history_days` frente al máximo de sus `n` sesiones
    start = max(n - 1, len(candles) - cfg.history_days)
    hist = [(m - closes[i]) / m * 100 for i in range(start, len(candles) - 1) if (m := max(highs[i - n + 1:i + 1])) > 0]
    pctl = sum(1 for h in hist if h <= drop) / len(hist) * 100 if len(hist) >= cfg.min_history else None
    expected = iv30_pct * math.sqrt(n / SESSIONS_PER_YEAR) if iv30_pct and iv30_pct > 0 else None
    return DropStats(drop, atr_mult, pctl, drop / expected if expected else None)
