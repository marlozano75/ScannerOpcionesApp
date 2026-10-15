"""Perfil del Scanner al que pertenece una put vendida del Panel, según el DTE con el que se abrió.

Cada perfil tiene un rango de DTE (el mínimo y el máximo de sus filtros). Gana el perfil cuyo rango contiene el DTE de apertura y, si son
varios, el más estrecho (el más específico); a igualdad, el primero de la lista. Si ninguno encaja, la put va al perfil «Tortuga»
(`FALLBACK`), que no se clasifica por DTE. Funciones puras."""
from __future__ import annotations

from typing import Optional, Sequence

from scanner_opciones.scanner.profiles import Profile

FALLBACK = "Tortuga"


def dte_range(p: Profile) -> Optional[tuple[int, int]]:
    """(DTE mín., DTE máx.) de los filtros de los instrumentos activos del perfil, o None si no los define."""
    lows, highs = [], []
    for inst in p.instruments:
        params = p.spread if inst == "spread" else p.put
        try:
            lo, hi = int(params["dte_min"]), int(params["dte_max"])
        except (KeyError, ValueError):
            continue
        lows.append(lo)
        highs.append(hi)
    return (min(lows), max(highs)) if lows else None


def classify_dte(dte_open: int, profiles: Sequence[Profile]) -> str:
    """Nombre del perfil al que corresponde un DTE de apertura."""
    best: Optional[tuple[int, int, str]] = None
    for i, p in enumerate(profiles):
        rng = None if p.name == FALLBACK else dte_range(p)
        if rng is not None and rng[0] <= dte_open <= rng[1]:
            key = (rng[1] - rng[0], i, p.name)
            if best is None or key < best:
                best = key
    return best[2] if best else FALLBACK
