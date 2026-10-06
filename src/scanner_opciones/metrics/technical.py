"""Análisis técnico sobre cierres diarios: medias, tendencia, zonas de soporte y días desde el último toque.

Todo son funciones puras sobre `Bars` = [(día, cierre), ...] en orden ascendente. Solo se usan cierres
(el histórico guardado no tiene máximos ni mínimos intradía).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

Bars = Sequence[tuple[date, float]]

FRAMES = ("daily", "weekly", "monthly")


def sma(values: Sequence[float], n: int) -> Optional[float]:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def ema(values: Sequence[float], n: int) -> Optional[float]:
    """Media móvil exponencial de periodo `n`, sembrada con la media simple de los `n` primeros cierres."""
    if n <= 0 or len(values) < n:
        return None
    k = 2 / (n + 1)
    out = sum(values[:n]) / n
    for v in values[n:]:
        out = v * k + out * (1 - k)
    return out


def resample(bars: Bars, frame: str) -> list[tuple[date, float]]:
    """Cierres por ventana: 'daily' (sin cambios), 'weekly' (último cierre de cada semana ISO) o
    'monthly' (último cierre de cada mes). La última ventana puede estar incompleta (periodo en curso)."""
    if frame == "daily":
        return list(bars)
    if frame not in FRAMES:
        raise ValueError(f"ventana desconocida: {frame}")
    key = (lambda d: d.isocalendar()[:2]) if frame == "weekly" else (lambda d: (d.year, d.month))
    out: list[tuple[date, float]] = []
    last_key = None
    for d, px in bars:
        k = key(d)
        if k == last_key:
            out[-1] = (d, px)
        else:
            out.append((d, px))
            last_key = k
    return out


def last_months(bars: Bars, today: date, months: int) -> list[tuple[date, float]]:
    """Barras de los últimos `months` meses (30,4 días por mes)."""
    cutoff = today - timedelta(days=round(months * 30.4375))
    return [(d, px) for d, px in bars if d >= cutoff]


def pivots(values: Sequence[float], width: int) -> tuple[list[int], list[int]]:
    """Índices de los máximos y mínimos locales: un valor es pivote si supera (máximo) o queda por debajo
    (mínimo) de los `width` valores anteriores y no es superado por los `width` siguientes. Los últimos
    `width` valores no se pueden confirmar todavía."""
    highs: list[int] = []
    lows: list[int] = []
    for i in range(width, len(values) - width):
        left, right, v = values[i - width:i], values[i + 1:i + 1 + width], values[i]
        if all(v > x for x in left) and all(v >= x for x in right):
            highs.append(i)
        if all(v < x for x in left) and all(v <= x for x in right):
            lows.append(i)
    return highs, lows


# ---- tendencia ------------------------------------------------------------------------------------
def trend_unbroken_extreme(
    bars: Bars, up: bool, price: float, today: date, min_age_days: int, min_progress_pct: float,
) -> tuple[bool, str]:
    """Alcista: el mínimo del histórico (que el precio no ha vuelto a romper en ningún cierre posterior) debe
    tener al menos `min_age_days` de antigüedad y el precio debe haber avanzado `min_progress_pct` % desde él.
    Bajista: la imagen especular con el máximo."""
    if not bars:
        return False, "sin histórico"
    pick = min if up else max
    day, extreme = pick(bars, key=lambda b: b[1])        # ante empates, el más antiguo
    age = (today - day).days
    if age < min_age_days:
        return False, f"{'mínimo' if up else 'máximo'} de hace solo {age} días (< {min_age_days})"
    progress = (price / extreme - 1) * 100 if up else (extreme / price - 1) * 100
    if progress < min_progress_pct:
        return False, f"avance {progress:.1f} % desde el {'mínimo' if up else 'máximo'} (< {min_progress_pct:g} %)"
    return True, ""


def trend_swings(
    bars: Bars, up: bool, price: float, pivot_width: int, required: int,
) -> tuple[bool, str]:
    """Con los cierres diarios de `bars`. Alcista: los últimos `required` máximos y mínimos locales son crecientes
    y **ningún cierre posterior al último mínimo, ni el precio actual, lo ha vuelto a romper**. Bajista: máximos
    y mínimos decrecientes y ningún cierre posterior al último máximo lo ha superado."""
    values = [px for _, px in bars]
    highs, lows = pivots(values, pivot_width)
    if len(highs) < required or len(lows) < required:
        return False, "pocos máximos/mínimos para evaluar"
    hv, lv = [values[i] for i in highs[-required:]], [values[i] for i in lows[-required:]]
    if up:
        ok = all(b > a for a, b in zip(hv, hv[1:])) and all(b > a for a, b in zip(lv, lv[1:]))
        broken = min(values[lows[-1] + 1:] + [price]) < lv[-1]
    else:
        ok = all(b < a for a, b in zip(hv, hv[1:])) and all(b < a for a, b in zip(lv, lv[1:]))
        broken = max(values[highs[-1] + 1:] + [price]) > hv[-1]
    if not ok:
        return False, "máximos y mínimos " + ("no crecientes" if up else "no decrecientes")
    if broken:
        return False, "se ha roto el último " + ("mínimo" if up else "máximo")
    return True, ""


# ---- soporte --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    touches: int
    clusters: int
    first_touch: date


def support_zones(
    bars: Bars, today: date, lookback_days: int, band_pct: float, min_touches: int, min_clusters: int,
    pivot_width: int, cluster_gap_days: int,
) -> list[Zone]:
    """Zonas de soporte probadas: nivel que el precio ha tocado `min_touches`+ veces (mínimos locales dentro de
    una banda del `band_pct` %) en `min_clusters`+ episodios separados (≥ `cluster_gap_days` días entre
    ellos) durante `lookback_days`, sin ningún cierre posterior al primer toque que lo pierda en más de
    `band_pct` %."""
    window = [(d, px) for d, px in bars if (today - d).days <= lookback_days]
    _, low_idx = pivots([px for _, px in window], pivot_width)
    touches = sorted((window[i][1], window[i][0]) for i in low_idx)       # por precio
    zones: list[list[tuple[float, date]]] = []
    for px, d in touches:
        if zones and px <= zones[-1][0][0] * (1 + band_pct / 100):
            zones[-1].append((px, d))
        else:
            zones.append([(px, d)])
    out: list[Zone] = []
    for members in zones:
        if len(members) < min_touches:
            continue
        days = sorted(d for _, d in members)
        clusters = 1 + sum(1 for a, b in zip(days, days[1:]) if (b - a).days >= cluster_gap_days)
        if clusters < min_clusters:
            continue
        low, high = min(px for px, _ in members), max(px for px, _ in members)
        if any(d > days[0] and px < low * (1 - band_pct / 100) for d, px in window):
            continue                                                       # el nivel se ha perdido
        out.append(Zone(low, high, len(members), clusters, days[0]))
    return out


def best_support(zones: Sequence[Zone], price: float) -> Optional[Zone]:
    """La zona más alta por debajo del precio actual."""
    below = [z for z in zones if z.high < price]
    return max(below, key=lambda z: z.high) if below else None


# ---- strike ---------------------------------------------------------------------------------------
def days_since_touch(bars: Bars, strike: float, today: date) -> Optional[int]:
    """Días desde el último cierre en o por debajo del strike; None si no ha ocurrido en el histórico."""
    for d, px in reversed(bars):
        if px <= strike:
            return (today - d).days
    return None
