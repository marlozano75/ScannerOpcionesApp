"""Spreads de puts (bull put spread): put vendida + put comprada del mismo ticker, vencimiento y multiplicador, con strike inferior.

IBKR da cada pata como una posición suelta. Para medir el riesgo hay que emparejarlas: el riesgo máximo de un spread es su ancho
(strike vendido − strike comprado) × multiplicador × contratos, no el nominal completo de la pata vendida.
El emparejado es automático (las patas no traen etiqueta de estrategia): cada put vendida toma, entre las compradas del mismo
vencimiento con strike inferior, primero la de strike más alto (el spread más estrecho). Lo que no se empareja queda como está.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import Position


@dataclass(frozen=True)
class Spread:
    short: Position      # cantidad = −contratos emparejados; valor de mercado prorrateado
    long: Position       # cantidad = +contratos emparejados; valor de mercado prorrateado

    @property
    def contracts(self) -> int:
        return int(abs(self.short.quantity))

    @property
    def width(self) -> float:
        return self.short.option.strike - self.long.option.strike

    def combined(self) -> Position:
        """La pata vendida con el valor y la prima netos del spread y `hedge_strike` puesto: su nominal es el ancho."""
        s, l = self.short, self.long
        avg = s.avg_cost - l.avg_cost if s.avg_cost is not None and l.avg_cost is not None else None
        return replace(s, market_value=s.market_value + l.market_value, avg_cost=avg, hedge_strike=l.option.strike)


def _is_put(p: Position) -> bool:
    return p.option is not None and p.option.right is OptionRight.PUT


def _part(p: Position, qty: float) -> Position:
    """Fracción `qty` (con el signo de p) de una posición."""
    ratio = abs(qty) / abs(p.quantity)
    return replace(p, quantity=qty, market_value=p.market_value * ratio)


def split_spreads(positions: Sequence[Position]) -> tuple[list[Position], list[Spread]]:
    """(posiciones que no forman spread, spreads). Las patas emparejadas salen de la primera lista."""
    groups: dict[tuple, list[Position]] = defaultdict(list)
    rest: list[Position] = []
    for p in positions:
        if _is_put(p) and p.quantity != 0:
            groups[(p.ticker, p.option.expiry, p.option.multiplier)].append(p)
        else:
            rest.append(p)
    spreads: list[Spread] = []
    for legs in groups.values():
        shorts = sorted((p for p in legs if p.quantity < 0), key=lambda p: -p.option.strike)
        longs = [[p, abs(p.quantity)] for p in sorted((p for p in legs if p.quantity > 0), key=lambda p: -p.option.strike)]
        for s in shorts:
            left = abs(s.quantity)
            for entry in longs:
                lp, avail = entry
                if left <= 0:
                    break
                if avail <= 0 or lp.option.strike >= s.option.strike:
                    continue
                n = min(left, avail)
                spreads.append(Spread(_part(s, -n), _part(lp, n)))
                entry[1] -= n
                left -= n
            if left > 0:
                rest.append(s if left == abs(s.quantity) else _part(s, -left))
        for lp, avail in longs:
            if avail > 0:
                rest.append(lp if avail == abs(lp.quantity) else _part(lp, avail))
    return rest, spreads


def with_spreads(positions: Sequence[Position]) -> list[Position]:
    """Las posiciones con cada spread fundido en una sola put vendida cuyo nominal es el ancho (para exposición y apalancamiento)."""
    rest, spreads = split_spreads(positions)
    return rest + [s.combined() for s in spreads]


def spread_label(p: Position) -> Optional[str]:
    return None if p.hedge_strike is None else f"{p.option.strike:g}/{p.hedge_strike:g}"
