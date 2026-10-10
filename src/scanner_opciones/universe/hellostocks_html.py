"""Lectura de la página de estrategias de HelloStocks guardada a mano desde el navegador (.html).

Sustituye al .xlsx equivalente: cada estrategia desplegada al guardar es una tabla del DOM y pasa a ser una
fuente (su nombre es el de la pestaña del antiguo libro, para que un fichero nuevo sustituya a la fuente en vez
de añadir otra). La app solo lee el fichero local: no se conecta a la web (sus términos prohíben el scraping).
Funciones puras, sin red. Solo cuentan las tablas del DOM: los datos incrustados (`strategyArray`) pueden ser de
otra carga y se usan únicamente para saber qué estrategias declara la página (`missing_strategies`).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from scanner_opciones.domain.errors import WatchlistError

HTML_SUFFIXES = {".html", ".htm"}
# Estrategia (título de la página) -> nombre de hoja/fuente del libro de HelloStocks que se cargaba antes.
SOURCE_NAMES = {
    "Lower Risk (Hello Stocks)": "Lower Risk (Hello Stocks)",
    "Balanced Risk (Hello Stocks)": "Balanced Risk (Hello Stocks)",
    "Full Throttle (Hello Stocks)": "Full Throttle (Hello Stocks)",
    "Value Investing (Warren Buffett)": "Value Investing-Warren Buffett",
    "Defensive Investing (Benjamin Graham)": "Defensive Investing-Benjamin G",
    "Growth at a Reasonable Price (Peter Lynch)": "GrowthReasonablePrice_P.Lynch",
    "Growth Investing (Philip Fisher)": "Growth Inv. Philip Fisher",
    "Magic Formula Investing (Joel Greenblatt)": "Magic Formula Joel Greenblatt",
    "Contrarian Investing (David Dreman)": "Contrarian Inv David Drem",
    "Price/Sales Ratio Focus (Ken Fisher)": "Price SalesRatioFocus KenFisher",
    "Deep Value Investing (Seth Klarman)": "Deep Value Inv. Seth Klarman",
}
_DROP_COLUMNS = {"hellostocks score"}              # columna «solo miembros»
_BLOCK = re.compile(r'<div id="([^"]+\([^"]+\))"')  # cada estrategia: <div id="Nombre (Autor)">


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _plain(header: str) -> str:
    return re.sub(r"[^\w ]", "", header).strip().lower()   # quita el candado «🔒»


def read_tables(page: str) -> dict[str, list[list[str]]]:
    """{estrategia: filas (la primera es la cabecera)} de cada bloque con tabla, sin la columna de miembros."""
    starts = [(m.start(), m.group(1)) for m in _BLOCK.finditer(page)]
    out: dict[str, list[list[str]]] = {}
    for k, (pos, name) in enumerate(starts):
        segment = page[pos: starts[k + 1][0] if k + 1 < len(starts) else len(page)]
        table = re.search(r"<table.*?</table>", segment, re.S)
        if not table:
            continue
        trs = re.findall(r"<tr.*?</tr>", table.group(0), re.S)
        if not trs:
            continue
        header = [_text(c) for c in re.findall(r"<th.*?</th>", trs[0], re.S)]
        keep = [i for i, h in enumerate(header) if _plain(h) not in _DROP_COLUMNS]
        rows = [[_text(c) for c in re.findall(r"<td.*?</td>", r, re.S)] for r in trs[1:]]
        rows = [r for r in rows if any(r)]
        out[name] = [[header[i] for i in keep]] + [[r[i] if i < len(r) else "" for i in keep] for r in rows]
    return out


def declared_strategies(page: str) -> list[str]:
    """Estrategias que la página declara en sus datos incrustados, se muestren o no."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page, re.S)
    try:
        data = "".join(json.loads(f'"{c}"') for c in chunks)
        i = data.find('"strategyArray":')
        if i < 0:
            return []
        arr, _ = json.JSONDecoder().raw_decode(data[data.index("[", i):])
        return [a["name"] for a in arr]
    except (ValueError, KeyError, TypeError):
        return []


def _read(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise WatchlistError(f"No existe el archivo: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


def missing_strategies(path: str | Path) -> list[str]:
    """Avisos sobre lo que el guardado no trae: estrategias declaradas sin tabla y tablas con menos filas que su
    «Holdings: N» (listas a medio cargar)."""
    page = _read(path)
    tables = read_tables(page)
    warnings = [f"«{name}» no está en el HTML guardado (no estaba desplegada o la web no la muestra)"
                for name in declared_strategies(page) if name not in tables]
    for m in _BLOCK.finditer(page):
        name = m.group(1)
        if name not in tables:
            continue
        end = page.find('<div id="', m.end())
        held = re.search(r"Holdings: (\d+)", page[m.start(): end if end > 0 else len(page)])
        if held and int(held.group(1)) != len(tables[name]) - 1:
            warnings.append(f"«{name}» declara {held.group(1)} posiciones y solo hay {len(tables[name]) - 1} filas")
    return warnings


def load_html_sheets(path: str | Path) -> list[tuple[str, list[list[str]]]]:
    """(nombre de la fuente, filas con cabecera) por estrategia, para `sources.sheet_to_source`."""
    tables = read_tables(_read(path))
    if not tables:
        raise WatchlistError("El HTML no contiene ninguna tabla de estrategias de HelloStocks "
                             "(guarda la página con las listas desplegadas)")
    return [(SOURCE_NAMES.get(name, name)[:31], rows) for name, rows in tables.items()]
