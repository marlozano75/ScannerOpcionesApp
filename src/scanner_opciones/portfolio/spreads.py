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


@dataclass(frozen=True)
class Combo:
    """Combinación de opciones que no es un bull put spread: spread vertical de calls o de puts compradas (débito), calendario,
    diagonal, straddle o strangle. `legs` lleva cada pata con la cantidad y el valor prorrateados."""
    name: str
    legs: tuple[Position, ...]

    @property
    def contracts(self) -> int:
        return int(abs(self.legs[0].quantity))


def _is_option(p: Position) -> bool:
    return p.option is not None and p.quantity != 0


def _pair(pool: list[list], pick, accept) -> list[tuple[Position, Position, float]]:
    """Empareja patas de `pool` (listas [posición, cantidad libre]): para cada una, en orden, la mejor candidata según `pick`
    entre las que cumplen `accept(a, b)`. Devuelve (a, b, contratos)."""
    pairs = []
    for ent in pool:
        a = ent[0]
        while ent[1] > 0:
            cands = [e for e in pool if e is not ent and e[1] > 0 and accept(a, e[0])]
            if not cands:
                break
            e = min(cands, key=lambda c: pick(a, c[0]))
            n = min(ent[1], e[1])
            pairs.append((a, e[0], n))
            ent[1] -= n
            e[1] -= n
    return pairs


def _name_vertical(long: Position, short: Position) -> str:
    call = long.option.right is OptionRight.CALL
    debit = (long.option.strike < short.option.strike) if call else (long.option.strike > short.option.strike)
    if call:
        return f"Call {'Debit' if debit else 'Credit'} Spread"
    return "Bear Put Spread" if debit else "Bull Put Spread"


def split_combos(positions: Sequence[Position]) -> tuple[list[Position], list[Combo]]:
    """(posiciones que no forman combo, combos). Se aplica a lo que deja `split_spreads`. Orden de emparejado: verticales
    (mismo vencimiento y derecho), straddle/strangle (mismo vencimiento, call y put del mismo signo) y calendarios/diagonales
    (mismo derecho, distinto vencimiento, signo opuesto); dentro de cada paso, las patas con strikes más cercanos primero."""
    opts = [[p, abs(p.quantity)] for p in positions if _is_option(p)]
    rest = [p for p in positions if not _is_option(p)]
    combos: list[Combo] = []

    def run(group_key, accept, pick, name):
        groups: dict[tuple, list[list]] = defaultdict(list)
        for ent in opts:
            if ent[1] > 0:
                groups[group_key(ent[0])].append(ent)
        for pool in groups.values():
            for a, b, n in _pair(pool, pick, accept):
                legs = (_part(a, n if a.quantity > 0 else -n), _part(b, n if b.quantity > 0 else -n))
                combos.append(Combo(name(a, b), legs))

    near = lambda a, b: abs(a.option.strike - b.option.strike)
    opposite = lambda a, b: (a.quantity > 0) != (b.quantity > 0) and a.option.strike != b.option.strike
    run(lambda p: (p.ticker, p.option.expiry, p.option.multiplier, p.option.right),
        lambda a, b: a.quantity > 0 and b.quantity < 0 and opposite(a, b), near,
        lambda a, b: _name_vertical(a, b))
    run(lambda p: (p.ticker, p.option.expiry, p.option.multiplier),
        lambda a, b: a.option.right is OptionRight.CALL and b.option.right is OptionRight.PUT and (a.quantity > 0) == (b.quantity > 0),
        near, lambda a, b: f"{'Straddle' if a.option.strike == b.option.strike else 'Strangle'} {'comprado' if a.quantity > 0 else 'vendido'}")
    run(lambda p: (p.ticker, p.option.multiplier, p.option.right),
        lambda a, b: a.quantity > 0 and b.quantity < 0 and a.option.expiry != b.option.expiry,
        lambda a, b: (abs(a.option.strike - b.option.strike), abs((a.option.expiry - b.option.expiry).days)),
        lambda a, b: "Calendar Spread" if a.option.strike == b.option.strike else "Diagonal Spread")
    for p, left in opts:
        if left > 0:
            rest.append(p if left == abs(p.quantity) else _part(p, left if p.quantity > 0 else -left))
    return rest, combos


def with_spreads(positions: Sequence[Position]) -> list[Position]:
    """Las posiciones con cada spread fundido en una sola put vendida cuyo nominal es el ancho (para exposición y apalancamiento).
    Las patas de otros combos (`split_combos`) no salen: no son puts vendidas sueltas."""
    rest, spreads = split_spreads(positions)
    rest, _ = split_combos(rest)
    return rest + [s.combined() for s in spreads]


def spread_label(p: Position) -> Optional[str]:
    return None if p.hedge_strike is None else f"{p.option.strike:g}/{p.hedge_strike:g}"
