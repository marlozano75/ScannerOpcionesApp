"""Filtros por columna sobre una tabla de RankedStocks. Funciones puras.

Parámetros de la URL, por índice de columna: texto `t{i}` (contiene), elección `c{i}` (uno o varios
valores), numérica `min{i}` / `max{i}`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from scanner_opciones.rankedstocks.loader import CHOICE, NUMBER, TEXT, RankedTable, Row, parse_number


@dataclass(frozen=True)
class Filters:
    text: dict[int, str] = field(default_factory=dict)
    choice: dict[int, frozenset[str]] = field(default_factory=dict)
    ranges: dict[int, tuple[Optional[float], Optional[float]]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.text or self.choice or self.ranges)


def _bound(raw: str, label: str) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return None
    value = parse_number(raw)
    if value is None:
        raise ValueError(f"{label}: «{raw}» no es un número válido (admite $, comas y K/M/B/T, p. ej. 500M)")
    return value


def parse_filters(table: RankedTable, qp) -> Filters:
    """Lee los filtros de `qp` (query string). Lanza ValueError si un número no es válido."""
    text: dict[int, str] = {}
    choice: dict[int, frozenset[str]] = {}
    ranges: dict[int, tuple[Optional[float], Optional[float]]] = {}
    for i, col in enumerate(table.columns):
        if col.kind == TEXT:
            value = (qp.get(f"t{i}") or "").strip()
            if value:
                text[i] = value
        elif col.kind == CHOICE:
            picked = frozenset(v for v in qp.getlist(f"c{i}") if v in col.choices)
            if picked:
                choice[i] = picked
        elif col.kind == NUMBER:
            lo, hi = _bound(qp.get(f"min{i}"), f"{col.name} mín."), _bound(qp.get(f"max{i}"), f"{col.name} máx.")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{col.name}: el mínimo no puede superar el máximo")
            if lo is not None or hi is not None:
                ranges[i] = (lo, hi)
    return Filters(text, choice, ranges)


def apply_filters(table: RankedTable, filters: Filters) -> list[Row]:
    out: list[Row] = []
    for row in table.rows:
        ok = all(needle.lower() in row.cells[i].text.lower() for i, needle in filters.text.items())
        ok = ok and all(row.cells[i].text in picked for i, picked in filters.choice.items())
        for i, (lo, hi) in filters.ranges.items():
            if not ok:
                break
            v = row.cells[i].value
            ok = v is not None and (lo is None or v >= lo) and (hi is None or v <= hi)
        if ok:
            out.append(row)
    return out
