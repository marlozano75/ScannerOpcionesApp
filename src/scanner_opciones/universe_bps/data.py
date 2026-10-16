"""Formato (versión 1) del universo para bull put spreads: `data/universe/universo_bps.json`.

{"version": 1, "generated_at": "2026-10-17T10:00:00+02:00", "warnings": [...],
 "members": [{"ticker": "SPY", "kind": "ETF", "sector": "Índice EE.UU.", "level": "A", "radar": true, "note": "..."}]}

`level`: A ETF núcleo, B megacaps, C diversificación sectorial, D especulativos (no entran en la fuente del Universo).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LEVELS = ("A", "B", "C", "D")
KINDS = ("ETF", "Acción")
ACTIVE_LEVELS = ("A", "B", "C")     # el D está desactivado por defecto


class UniverseBpsError(ValueError):
    """JSON del universo de spreads mal formado."""


@dataclass(frozen=True)
class Member:
    ticker: str
    kind: str                 # «ETF» o «Acción»
    sector: str
    level: str                # A-D
    radar: bool = True        # ¿lo cubre el Market Radar? (si no, no se le aplica la histéresis)
    note: str = ""

    @property
    def active(self) -> bool:
        return self.level in ACTIVE_LEVELS


@dataclass(frozen=True)
class SpreadUniverse:
    generated_at: datetime
    members: tuple[Member, ...]
    warnings: tuple[str, ...] = field(default=())

    def active(self) -> list[Member]:
        return [m for m in self.members if m.active]

    def etfs(self) -> set[str]:
        return {m.ticker for m in self.members if m.kind == "ETF"}


def parse_member(raw: Any) -> Member:
    if not isinstance(raw, dict):
        raise UniverseBpsError("un miembro no es un objeto")
    ticker = str(raw.get("ticker", "")).strip().upper()
    if not ticker:
        raise UniverseBpsError("un miembro no tiene ticker")
    kind, level = raw.get("kind"), raw.get("level")
    if kind not in KINDS:
        raise UniverseBpsError(f"{ticker}: kind debe ser uno de {KINDS}")
    if level not in LEVELS:
        raise UniverseBpsError(f"{ticker}: level debe ser uno de {LEVELS}")
    return Member(ticker, kind, str(raw.get("sector", "")), level, bool(raw.get("radar", True)), str(raw.get("note", "")))


def parse_universe(doc: Any) -> SpreadUniverse:
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise UniverseBpsError("versión no soportada (se espera version 1)")
    try:
        generated = datetime.fromisoformat(str(doc["generated_at"]))
    except (KeyError, ValueError) as exc:
        raise UniverseBpsError("generated_at falta o no es una fecha ISO") from exc
    raw_members = doc.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise UniverseBpsError("no hay ningún miembro")
    members = tuple(parse_member(m) for m in raw_members)
    seen = [m.ticker for m in members]
    if len(set(seen)) != len(seen):
        raise UniverseBpsError("hay tickers repetidos")
    return SpreadUniverse(generated, members, tuple(str(w) for w in doc.get("warnings", [])))


def load_universe(path: "str | Path") -> SpreadUniverse:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UniverseBpsError(f"no se pudo leer {path}: {exc}") from exc
    return parse_universe(doc)
