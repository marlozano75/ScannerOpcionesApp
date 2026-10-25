"""Sincronización Universo → Watchlist: la Watchlist es la unión de las fuentes del Universo que tienen opciones (función pura).

Los tickers se comparan por su nombre limpio (`clean_ticker`: «BRK-B» y «BRK» son el mismo), como hace el Universo."""
from __future__ import annotations

from dataclasses import dataclass

from scanner_opciones.rankedstocks.loader import clean_ticker

GUARD_MIN = 30           # una pasada automática que quite más de esto y más del GUARD_FRACTION de la Watchlist pide confirmación
GUARD_FRACTION = 0.25


@dataclass(frozen=True)
class SyncPlan:
    add: tuple[str, ...] = ()           # en alguna fuente y no en la Watchlist
    remove: tuple[str, ...] = ()        # en la Watchlist y en ninguna fuente
    protected: tuple[str, ...] = ()     # en ninguna fuente pero con posiciones abiertas o instrumento propio: se quedan
    kept: int = 0                       # ya en la Watchlist y en alguna fuente

    @property
    def empty(self) -> bool:
        return not self.add and not self.remove

    def exceeds_guard(self, watchlist_size: int) -> bool:
        return len(self.remove) > max(GUARD_MIN, int(watchlist_size * GUARD_FRACTION))


def plan(desired, watchlist, protected=()) -> SyncPlan:
    """Qué añadir y qué quitar para que `watchlist` sea `desired` (tickers de las fuentes). Sin tickers deseados no se propone nada: lo
    más probable es que las fuentes no se hayan cargado, no que se quiera vaciar la Watchlist."""
    wanted = {clean_ticker(t): t for t in sorted(desired)}
    if not wanted:
        return SyncPlan()
    have = {clean_ticker(t): t for t in watchlist}
    hold = {clean_ticker(t) for t in protected}
    add = tuple(t for k, t in wanted.items() if k not in have)
    gone = [t for k, t in have.items() if k not in wanted]
    remove = tuple(t for t in gone if clean_ticker(t) not in hold)
    keep = tuple(t for t in gone if clean_ticker(t) in hold)
    return SyncPlan(add, remove, keep, sum(1 for k in have if k in wanted))
