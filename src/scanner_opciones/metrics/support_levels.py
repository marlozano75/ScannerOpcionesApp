"""Soportes horizontales validados por los mínimos de las velas (columnas «Soporte» y «Strike/Sop.» del Scanner; informativo, no filtra).

Un soporte es un grupo de mínimos locales (pivotes de ±`pivot_width` velas) dentro de una banda del `band_pct` % que el precio no ha
perdido nunca (ningún cierre por debajo de la banda tras el primer pivote). Cuantos más pivotes, más veces lo ha respetado: con la
watchlist completa (2.050 tickers, 2023-2026, fuera de muestra) el rebote a 3 sesiones sube del 44 % (1 pivote) al 58 % (2), 67 % (3) y 69 % (4+)
(`scripts/suelos/medias_soportes.py` y `soportes2.py`; las medias móviles, en cambio, no se respetan de forma persistente: `medias2.py`).
Funciones puras sobre velas (día, apertura, máximo, mínimo, cierre) ascendentes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import SupportLevelSettings


@dataclass(frozen=True)
class SupportLevel:
    low: float                 # mínimo más bajo del grupo
    high: float                # mínimo más alto del grupo (techo de la zona)
    pivots: int                # mínimos locales del grupo (toques que ha respetado)
    approaches: int            # veces que el mínimo volvió a llegar a la zona viniendo de arriba
    bounces: int               # de ellas, las que cerraron sobre la zona y siguieron sobre ella `hold_sessions` después
    last_pivot: date


def support_levels(candles: Sequence[tuple], cfg: SupportLevelSettings) -> list[SupportLevel]:
    """Soportes no perdidos del histórico de `candles`, de menor a mayor precio."""
    w, band, hold = cfg.pivot_width, cfg.band_pct / 100, cfg.hold_sessions
    n = len(candles)
    if n < 2 * w + 2:
        return []
    low = [c[3] for c in candles]
    close = [c[4] for c in candles]
    # el primer par de comparaciones es condición necesaria de las otras dos (un mínimo local está bajo la sesión anterior y no sobre la
    # siguiente) y descarta casi todas las sesiones sin crear las dos listas de la ventana: 0,71 s → 0,41 s con toda la watchlist (2026-11-13)
    piv = [i for i in range(w, n - w) if low[i] < low[i - 1] and low[i] <= low[i + 1]
           and low[i] < min(low[i - w:i]) and low[i] <= min(low[i + 1:i + 1 + w])]
    groups: list[list[int]] = []
    for i in sorted(piv, key=lambda k: low[k]):
        if groups and low[i] <= low[groups[-1][0]] * (1 + band):
            groups[-1].append(i)
        else:
            groups.append([i])
    # cierre mínimo desde cada sesión hasta el final: saber si un grupo se perdió deja de recorrer todos los cierres posteriores (era la mitad del coste)
    later_min = [float("inf")] * (n + 1)
    for i in range(n - 1, -1, -1):
        later_min[i] = close[i] if close[i] < later_min[i + 1] else later_min[i + 1]
    out = []
    for g in groups:
        lo, hi = min(low[i] for i in g), max(low[i] for i in g)
        first = min(g)
        if later_min[first + 1] < lo * (1 - band):
            continue                                              # perdido: ya no es soporte
        approaches = bounces = 0
        for t in range(first + w + 1, n - hold):
            if low[t] <= hi * 1.003 and low[t - 1] > hi * 1.003 and close[t - 1] > hi:
                approaches += 1
                if close[t] >= lo and close[t + hold] > lo * (1 - band / 2):
                    bounces += 1
        out.append(SupportLevel(lo, hi, len(g), approaches, bounces, candles[max(g)][0]))
    return out


def nearest_support(levels: Sequence[SupportLevel], price: float, min_pivots: int,
                    max_distance_pct: Optional[float] = None) -> Optional[SupportLevel]:
    """El soporte validado (≥ `min_pivots` pivotes) más alto que queda por debajo del precio y, si se indica,
    a no más de `max_distance_pct` % de él (más lejos casi nunca llega a ser relevante: `scripts/suelos/soportes4.py`)."""
    below = [lv for lv in levels if lv.pivots >= min_pivots and lv.high < price
             and (max_distance_pct is None or (price / lv.high - 1) * 100 <= max_distance_pct)]
    return max(below, key=lambda lv: lv.high) if below else None
