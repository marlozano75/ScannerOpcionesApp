"""Gráficos sin librerías: HTML y SVG generados en el servidor (barras, medidores, líneas, columnas).

Reglas del método de visualización de datos que se siguen aquí:
  · marcas finas (barras ≤ 24 px con el extremo redondeado de 4 px, líneas de 2 px, puntos de ≥ 8 px) y 2 px de hueco
    entre piezas que se tocan;
  · una sola escala por gráfico; una sola tonalidad para magnitudes y la paleta categórica en orden fijo (máx. 7 + «Otros»);
  · leyenda siempre que haya dos o más series; etiquetas solo donde importan (la punta de la barra, el último punto);
  · tooltips con el valor primero (`data-tip="valor|etiqueta|detalle"`, los pone `base.html`), también con el teclado;
  · todo texto que viene de los datos se escapa.
Todas las funciones devuelven `Markup` listo para insertar en una plantilla.
"""
from __future__ import annotations

import math
from html import escape
from typing import Callable, Optional, Sequence

from markupsafe import Markup

SERIES = 7                                   # series categóricas distintas; el resto se agrupa en «Otros»
OTHER = "Otros"
STATUS = {"green": "#0ca30c", "amber": "#fab219", "orange": "#ec835a", "red": "#d03b3b", "unknown": "#898781"}


def _e(text) -> str:
    return escape(str(text), quote=True)


def _tip(*parts) -> str:
    return _e("|".join(str(p) for p in parts if p not in (None, "")))


def series_color(index: int) -> str:
    """Color de la serie `index` (0-6); la octava ranura (`SERIES`) es el gris de «Otros»."""
    return "var(--viz-other)" if index >= SERIES else f"var(--viz-{index + 1})"


def top_with_other(values: dict[str, float], limit: int = SERIES) -> list[tuple[str, float]]:
    """Las `limit` mayores series y el resto sumado en «Otros» (nunca se inventan más tonalidades)."""
    ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    head, tail = ranked[:limit], ranked[limit:]
    if tail:
        head.append((OTHER, sum(v for _, v in tail)))
    return head


def legend(items: Sequence[tuple[str, str]]) -> Markup:
    """Leyenda: un cuadradito del color de la serie y su nombre (el texto nunca lleva el color de la serie)."""
    if len(items) < 2:
        return Markup("")
    return Markup('<ul class="lg">' + "".join(
        f'<li><i style="background:{color}"></i>{_e(name)}</li>' for name, color in items) + "</ul>")


def empty(message: str = "Sin datos") -> Markup:
    return Markup(f'<p class="muted viz-empty">{_e(message)}</p>')


# ---------------------------------------------------------------------------------------------- barras horizontales
def hbars(rows: Sequence[tuple[str, float, str]], fmt: Callable[[float], str] = lambda v: f"{v:.1f}",
          color: str = "var(--viz-1)", max_value: Optional[float] = None, message: str = "Sin datos") -> Markup:
    """Una barra por fila (etiqueta, valor, detalle del tooltip); el valor va en la punta."""
    if not rows:
        return empty(message)
    top = max_value if max_value is not None else max((v for _, v, _ in rows), default=0) or 1
    out = ['<div class="hb">']
    for label, value, detail in rows:
        width = max(0.0, min(100.0, value / top * 100)) if top else 0
        out.append(
            f'<div class="hb-row" tabindex="0" data-tip="{_tip(fmt(value), label, detail)}">'
            f'<span class="hb-l" title="{_e(label)}">{_e(label)}</span>'
            f'<span class="hb-t"><span class="hb-f" style="width:{width:.1f}%;background:{color}"></span></span>'
            f'<span class="hb-v">{_e(fmt(value))}</span></div>')
    out.append("</div>")
    return Markup("".join(out))


def stacked_hbars(rows: Sequence[tuple[str, Sequence[tuple[str, float]], str]], order: Sequence[str],
                  fmt: Callable[[float], str] = lambda v: f"{v:.0f}", message: str = "Sin datos") -> Markup:
    """Una barra apilada por fila (etiqueta, [(serie, valor)], texto del total); la longitud es el total de la fila
    respecto al mayor. `order` fija el color de cada serie (la misma en todas las filas)."""
    if not rows or not any(sum(v for _, v in segs) > 0 for _, segs, _ in rows):
        return empty(message)
    colors = {name: series_color(i) for i, name in enumerate(order)}
    biggest = max(sum(v for _, v in segs) for _, segs, _ in rows) or 1
    out = [str(legend([(name, colors[name]) for name in order])), '<div class="hb sb">']
    for label, segs, total_text in rows:
        total = sum(v for _, v in segs)
        parts = "".join(
            f'<span class="sb-seg" tabindex="0" style="flex:{v:.4f} 1 0;background:{colors[name]}" '
            f'data-tip="{_tip(fmt(v), name, label)}"></span>' for name, v in segs if v > 0)
        out.append(
            f'<div class="hb-row"><span class="hb-l">{_e(label)}</span>'
            f'<span class="hb-t"><span class="sb-bar" style="width:{total / biggest * 100:.1f}%">{parts}</span></span>'
            f'<span class="hb-v">{_e(total_text)}</span></div>')
    out.append("</div>")
    return Markup("".join(out))


# ---------------------------------------------------------------------------------------------------- medidor
def meter(value: Optional[float], lo: float, hi: float, color: str, ticks: Sequence[tuple[float, str]] = (),
          label: str = "") -> Markup:
    """Medidor de una razón frente a unos límites: el relleno lleva el estado (color) y las marcas los umbrales."""
    if value is None or hi <= lo:
        return Markup("")
    pct = max(0.0, min(100.0, (value - lo) / (hi - lo) * 100))
    marks = "".join(f'<i class="mt-tick" style="left:{(t - lo) / (hi - lo) * 100:.1f}%"></i>' for t, _ in ticks)
    names = "".join(f'<span style="left:{(t - lo) / (hi - lo) * 100:.1f}%">{_e(text)}</span>' for t, text in ticks)
    return Markup(
        f'<div class="mt" role="meter" aria-valuemin="{lo:g}" aria-valuemax="{hi:g}" aria-valuenow="{value:g}" aria-label="{_e(label)}">'
        f'<div class="mt-track"><div class="mt-fill" style="width:{pct:.1f}%;background:{color}"></div>{marks}</div>'
        f'<div class="mt-labels">{names}</div></div>')


# ---------------------------------------------------------------------------------------------------- líneas
def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Marcas de eje en números redondos (1, 2, 2,5, 5 × 10ⁿ) que cubren [lo, hi] en unos `n` tramos."""
    span = (hi - lo) or abs(hi) or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start, end = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    return [round(start + i * step, 10) for i in range(int(round((end - start) / step)) + 1)]


def _scale(v: float, lo: float, hi: float, a: float, b: float) -> float:
    return a if hi == lo else a + (v - lo) / (hi - lo) * (b - a)


def line_chart(points: Sequence[tuple[float, float, str]], x_ticks: Sequence[tuple[float, str]],
               y_fmt: Callable[[float], str] = lambda v: f"{v:.1f}", width: int = 520, height: int = 150,
               label: str = "", markers: bool = False, message: str = "Sin datos suficientes") -> Markup:
    """Línea de 2 px con área al 10 % y el último punto marcado. `points`: (x, y, texto del tooltip). Cada punto tiene
    una columna de acierto más ancha que la marca, con línea guía y punto al pasar el ratón o enfocar."""
    if len(points) < 2:
        return empty(message)
    left, right, top, bottom = 40, 16, 12, 24
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    ylo, yhi = min(ys), max(ys)
    pad = (yhi - ylo) * 0.15 or abs(yhi) * 0.05 or 1.0
    ylo, yhi = ylo - pad, yhi + pad
    px = lambda x: _scale(x, min(xs), max(xs), left, width - right)      # noqa: E731
    py = lambda y: _scale(y, ylo, yhi, height - bottom, top)             # noqa: E731
    coords = [(px(x), py(y)) for x, y, _ in points]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{path} L{coords[-1][0]:.1f},{height - bottom} L{coords[0][0]:.1f},{height - bottom} Z"
    grid = "".join(
        f'<line class="gl" x1="{left}" x2="{width - right}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        f'<text class="ax" x="{left - 6}" y="{py(v) + 4:.1f}" text-anchor="end">{_e(y_fmt(v))}</text>'
        for v in (ylo + (yhi - ylo) * k / 2 for k in (0, 1, 2)))
    xlabels = "".join(
        f'<text class="ax" x="{px(x):.1f}" y="{height - 6}" text-anchor="{"start" if i == 0 else "end" if i == len(x_ticks) - 1 and len(x_ticks) > 1 else "middle"}">{_e(t)}</text>'
        for i, (x, t) in enumerate(x_ticks))
    hits = []
    for i, ((cx, cy), (_, _, tip)) in enumerate(zip(coords, points)):
        x0 = (coords[i - 1][0] + cx) / 2 if i else left
        x1 = (cx + coords[i + 1][0]) / 2 if i < len(coords) - 1 else width - right
        hits.append(
            f'<g class="pt" tabindex="0" data-tip="{_e(tip)}"><rect x="{x0:.1f}" y="{top}" width="{x1 - x0:.1f}" height="{height - top - bottom}" fill="transparent"/>'
            f'<line class="xh" x1="{cx:.1f}" x2="{cx:.1f}" y1="{top}" y2="{height - bottom}"/>'
            f'<circle class="dt" cx="{cx:.1f}" cy="{cy:.1f}" r="4.5"/></g>')
    dots = "".join(f'<circle class="mk" cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in (coords if markers else coords[-1:]))
    return Markup(
        f'<svg class="lc" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}">{grid}{xlabels}'
        f'<path class="ar" d="{area}"/><path class="ln" d="{path}"/>{dots}{"".join(hits)}</svg>')


# ---------------------------------------------------------------------------------------------------- columnas
def columns(items: Sequence[tuple[str, float, str]], width: int = 520, height: int = 170, label: str = "",
            fmt: Callable[[float], str] = lambda v: f"{v:.0f}", message: str = "Sin datos") -> Markup:
    """Columnas de ≤ 24 px con el extremo redondeado (4 px) y la base recta. `items`: (etiqueta, valor, detalle)."""
    if not items or not any(v for _, v, _ in items):
        return empty(message)
    left, right, top, bottom = 38, 8, 10, 26
    ticks = nice_ticks(0, max(v for _, v, _ in items) or 1, 2)
    top_value = ticks[-1] or 1
    slot = (width - left - right) / len(items)
    thick = min(24.0, slot * 0.7)
    y = lambda v: height - bottom - v / top_value * (height - top - bottom)   # noqa: E731
    grid = "".join(
        f'<line class="gl" x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
        f'<text class="ax" x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{_e(fmt(v))}</text>'
        for v in ticks)
    bars = []
    for i, (name, value, detail) in enumerate(items):
        cx, h = left + slot * (i + 0.5), value / top_value * (height - top - bottom)
        x0, r = cx - thick / 2, min(4.0, thick / 2, h)
        shape = (f'M{x0:.1f},{height - bottom} V{height - bottom - h + r:.1f} Q{x0:.1f},{height - bottom - h:.1f} {x0 + r:.1f},{height - bottom - h:.1f} '
                 f'H{x0 + thick - r:.1f} Q{x0 + thick:.1f},{height - bottom - h:.1f} {x0 + thick:.1f},{height - bottom - h + r:.1f} V{height - bottom} Z') if h > 0 else ""
        bars.append(
            f'<g class="cl" tabindex="0" data-tip="{_tip(fmt(value), name, detail)}"><rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{height - top - bottom}" fill="transparent"/>'
            f'<path class="bar1" d="{shape}"/><text class="ax" x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{_e(name)}</text></g>')
    return Markup(f'<svg class="lc" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}">{grid}{"".join(bars)}</svg>')
