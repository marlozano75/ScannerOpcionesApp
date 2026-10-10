"""Lectura de la página de estrategias de HelloStocks guardada a mano desde el navegador (.html).

Sustituye al .xlsx equivalente: cada estrategia desplegada al guardar es una tabla del DOM y pasa a ser una
fuente (su nombre es el de la pestaña del antiguo libro, para que un fichero nuevo sustituya a la fuente en vez
de añadir otra). Con «Strategy Criteria» abierto al guardar, cada estrategia trae además su tabla de criterios
(«Metric / Condition»), que se lee como `Source.criteria`. La app solo lee el fichero local: no se conecta a la web
(sus términos prohíben el scraping). Funciones puras, sin red. Solo cuentan las tablas del DOM: los datos incrustados
(`strategyArray`) pueden ser de otra carga y se usan únicamente para saber qué estrategias declara la página
(`missing_strategies`).
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
_PERCENT_METRICS = ("growth", "roe", "yield", "margin")   # HelloStocks da estas condiciones como fracción (0,5 = 50 %)


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def _plain(header: str) -> str:
    return re.sub(r"[^\w ]", "", header).strip().lower()   # quita el candado «🔒»


def _segments(page: str) -> dict[str, str]:
    """{estrategia: trozo de HTML de su bloque}."""
    starts = [(m.start(), m.group(1)) for m in _BLOCK.finditer(page)]
    return {name: page[pos: starts[k + 1][0] if k + 1 < len(starts) else len(page)]
            for k, (pos, name) in enumerate(starts)}


def _tables(segment: str) -> list[tuple[list[str], list[list[str]]]]:
    """(cabecera, filas) de cada tabla del bloque."""
    out = []
    for table in re.findall(r"<table.*?</table>", segment, re.S):
        trs = re.findall(r"<tr.*?</tr>", table, re.S)
        if trs:
            out.append(([_text(c) for c in re.findall(r"<th.*?</th>", trs[0], re.S)],
                        [[_text(c) for c in re.findall(r"<td.*?</td>", r, re.S)] for r in trs[1:]]))
    return out


def read_tables(page: str) -> dict[str, list[list[str]]]:
    """{estrategia: filas (la primera es la cabecera)} de la tabla de acciones de cada bloque (la de cabecera
    «Ticker»; con «Strategy Criteria» abierto hay otra antes), sin la columna de miembros."""
    out: dict[str, list[list[str]]] = {}
    for name, segment in _segments(page).items():
        table = next(((h, r) for h, r in _tables(segment) if h and _plain(h[0]) == "ticker"), None)
        if table is None:
            continue
        header, body = table
        keep = [i for i, h in enumerate(header) if _plain(h) not in _DROP_COLUMNS]
        rows = [r for r in body if any(r)]
        out[name] = [[header[i] for i in keep]] + [[r[i] if i < len(r) else "" for i in keep] for r in rows]
    return out


def format_condition(metric: str, condition: str) -> str:
    """«> 0.5» -> «> 50 %» (métricas en porcentaje); «>= 0 and < 1» -> «≥ 0 y < 1»."""
    percent = any(w in metric.lower() for w in _PERCENT_METRICS)

    def number(m: re.Match) -> str:
        v = float(m.group(0)) * (100 if percent else 1)
        text = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return text + (" %" if percent and v != 0 else "")

    out = re.sub(r"-?\d+(?:\.\d+)?", number, re.sub(r"^(\S+) < x < (\S+)$", r"entre \1 y \2", condition))
    return out.replace(">=", "≥").replace("<=", "≤").replace(" and ", " y ")


def read_criteria(page: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """{estrategia: ((métrica, condición), ...)} de la tabla «Metric / Condition» de cada bloque."""
    out: dict[str, tuple[tuple[str, str], ...]] = {}
    for name, segment in _segments(page).items():
        for header, rows in _tables(segment):
            if [_plain(h) for h in header[:2]] == ["metric", "condition"] and rows:
                out[name] = tuple((r[0], format_condition(r[0], r[1])) for r in rows if len(r) >= 2)
                break
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
    """Avisos sobre lo que el guardado no trae: estrategias declaradas sin tabla, tablas con menos filas que su
    «Holdings: N» (listas a medio cargar) y estrategias sin criterios («Strategy Criteria» sin abrir)."""
    page = _read(path)
    tables, criteria = read_tables(page), read_criteria(page)
    warnings = [f"«{name}» no está en el HTML guardado (no estaba desplegada o la web no la muestra)"
                for name in declared_strategies(page) if name not in tables]
    for name, segment in _segments(page).items():
        if name not in tables:
            continue
        held = re.search(r"Holdings: (\d+)", segment)
        if held and int(held.group(1)) != len(tables[name]) - 1:
            warnings.append(f"«{name}» declara {held.group(1)} posiciones y solo hay {len(tables[name]) - 1} filas")
        if name not in criteria:
            warnings.append(f"«{name}» no trae sus criterios (guarda la página con «Strategy Criteria» abierto)")
    return warnings


def load_html_sheets(path: str | Path) -> list[tuple[str, list[list[str]]]]:
    """(nombre de la fuente, filas con cabecera) por estrategia, para `sources.sheet_to_source`."""
    tables = read_tables(_read(path))
    if not tables:
        raise WatchlistError("El HTML no contiene ninguna tabla de estrategias de HelloStocks "
                             "(guarda la página con las listas desplegadas)")
    return [(SOURCE_NAMES.get(name, name)[:31], rows) for name, rows in tables.items()]


def load_html_criteria(path: str | Path) -> dict[str, tuple[tuple[str, str], ...]]:
    """{nombre de la fuente: criterios}, con los mismos nombres que `load_html_sheets`."""
    return {SOURCE_NAMES.get(name, name)[:31]: crit for name, crit in read_criteria(_read(path)).items()}
