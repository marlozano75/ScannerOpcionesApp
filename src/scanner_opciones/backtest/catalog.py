"""Catálogo de rondas de backtest que muestra la pestaña «Backtest» (`ui/routes_backtest.py`).

Los resultados viven en `resultados.json` (junto a este módulo): cada ronda es un experimento con su pregunta, sus tablas ya formateadas y su
veredicto. Añadir una ronda nueva es añadir un objeto a `rondas` (formato y ejemplo en `docs/backtest.md`); aquí solo se lee y se valida."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Optional

from markupsafe import Markup

RESULTS_PATH = Path(__file__).with_name("resultados.json")
OBJECTIVES = {"suelos": "Suelos", "cds": "Entrada CDS", "puts": "Vender puts", "datos": "Datos"}
VERDICTS = {"ventaja": "Con ventaja", "parcial": "Parcial", "sin_ventaja": "Sin ventaja", "info": "Información"}


class CatalogError(ValueError):
    """El fichero de resultados no tiene el formato esperado (se dice qué ronda y qué campo)."""


@dataclass(frozen=True)
class Bar:
    col: int
    max: float


@dataclass(frozen=True)
class Table:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    bar: Optional[Bar] = None
    note: str = ""


@dataclass(frozen=True)
class Round:
    id: str
    date: str
    title: str
    objective: str
    question: str
    verdict: str
    verdict_text: str
    tables: tuple[Table, ...] = ()
    points: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    doc: str = ""


@dataclass(frozen=True)
class Catalog:
    updated: str
    methodology: tuple[str, ...]
    bases: tuple[dict, ...]
    conclusions: tuple[str, ...]
    pending: tuple[str, ...]
    rounds: tuple[Round, ...] = field(default_factory=tuple)

    def counts(self) -> dict[str, int]:
        return {key: sum(1 for r in self.rounds if r.objective == key) for key in OBJECTIVES}


def _need(data: dict, key: str, where: str, kind=str):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise CatalogError(f"{where}: falta «{key}» o no es {kind.__name__}")
    return value


def _table(raw: dict, where: str) -> Table:
    columns = tuple(_need(raw, "columnas", where, list))
    rows = []
    for i, row in enumerate(_need(raw, "filas", where, list)):
        if not isinstance(row, list) or len(row) != len(columns):
            raise CatalogError(f"{where}, fila {i + 1}: tiene {len(row) if isinstance(row, list) else '?'} celdas y hay {len(columns)} columnas")
        rows.append(tuple(str(c) for c in row))
    bar = None
    if raw.get("barra") is not None:
        b = raw["barra"]
        if not isinstance(b.get("col"), int) or not 0 <= b["col"] < len(columns) or not isinstance(b.get("max"), (int, float)) or b["max"] <= 0:
            raise CatalogError(f"{where}: «barra» necesita col (0..{len(columns) - 1}) y max > 0")
        bar = Bar(b["col"], float(b["max"]))
    return Table(_need(raw, "titulo", where), tuple(str(c) for c in columns), tuple(rows), bar, str(raw.get("nota", "")))


def parse(data: dict) -> Catalog:
    rounds, seen = [], set()
    for raw in _need(data, "rondas", "resultados", list):
        rid = _need(raw, "id", "ronda")
        where = f"ronda {rid}"
        if rid in seen:
            raise CatalogError(f"{where}: id repetido")
        seen.add(rid)
        objective, verdict = _need(raw, "objetivo", where), _need(raw, "veredicto", where)
        if objective not in OBJECTIVES:
            raise CatalogError(f"{where}: objetivo «{objective}» no está en {sorted(OBJECTIVES)}")
        if verdict not in VERDICTS:
            raise CatalogError(f"{where}: veredicto «{verdict}» no está en {sorted(VERDICTS)}")
        rounds.append(Round(
            id=rid, date=_need(raw, "fecha", where), title=_need(raw, "titulo", where), objective=objective, question=_need(raw, "pregunta", where),
            verdict=verdict, verdict_text=_need(raw, "veredicto_texto", where),
            tables=tuple(_table(t, f"{where}, tabla {i + 1}") for i, t in enumerate(raw.get("tablas", []))),
            points=tuple(raw.get("puntos", [])), scripts=tuple(raw.get("scripts", [])), doc=str(raw.get("doc", ""))))
    return Catalog(updated=_need(data, "actualizado", "resultados"), methodology=tuple(data.get("metodologia", [])), bases=tuple(data.get("bases", [])),
                   conclusions=tuple(data.get("conclusiones", [])), pending=tuple(data.get("pendientes", [])), rounds=tuple(rounds))


def load(path: Path = RESULTS_PATH) -> Catalog:
    """Lee y valida el fichero (se vuelve a leer en cada petición: añadir una ronda no requiere reiniciar la app)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path.name}: JSON no válido ({exc})") from exc
    return parse(data)


# ------------------------------------------------------------------------------------------------------------- celdas
_NUMBER = re.compile(r"^[+\-−]?\d{1,3}(?:\.\d{3})+(?:,\d+)?|^[+\-−]?\d+(?:,\d+)?")


def first_number(text: str) -> Optional[float]:
    """Primer número al comienzo de la celda con el formato de la tabla («12,5 %», «+0,8 %», «106.000», «3,6 / 3,7»); None si empieza por texto."""
    m = _NUMBER.match(text.replace("**", "").strip())
    if not m:
        return None
    return float(m.group(0).replace("−", "-").replace(".", "").replace(",", "."))


def cell_html(text: str) -> Markup:
    """Texto de la celda escapado; `**negrita**` y `código` entre acentos graves son lo único que se interpreta."""
    out = escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return Markup(out)
