"""Fragmento HTML de la ficha de los gráficos con el historial de resultados (BPA real frente a estimación) y el dividendo. Funciones puras: todo texto
se escapa. Sin librerías: un SVG pequeño con un punto relleno por trimestre (real) y uno hueco (estimación)."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Optional, Sequence

from scanner_opciones.marketdata.history import Dividend, EarningsQuarter, beat_summary

WIDTH, HEIGHT = 360, 120
LEFT, RIGHT, TOP, BOTTOM = 38, 12, 12, 22


def _money(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _ticks(lo: float, hi: float) -> list[float]:
    """Tres marcas de eje redondas entre `lo` y `hi`."""
    span = hi - lo
    return [lo + span * i / 2 for i in range(3)]


def earnings_svg(quarters: Sequence[EarningsQuarter]) -> str:
    """Un punto relleno (real; verde si alcanzó la estimación, rojo si no, gris sin estimación) y uno hueco (estimación) por trimestre."""
    values = [v for q in quarters for v in (q.actual, q.estimate) if v is not None]
    lo, hi = min(0.0, min(values)), max(values)
    if hi <= lo:
        hi = lo + 0.01
    pad = (hi - lo) * 0.12
    hi += pad
    lo = lo - pad if lo < 0 else 0.0
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def y(v: float) -> float:
        return TOP + plot_h * (1 - (v - lo) / (hi - lo))

    slot = plot_w / len(quarters)
    parts = [f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="Resultados por acción: real frente a estimación" '
             'style="max-width:100%;height:auto">']
    for t in _ticks(lo, hi):
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="var(--line)" stroke-width="1"/>'
                     f'<text x="{LEFT - 5}" y="{y(t) + 3.5:.1f}" text-anchor="end" font-size="10" fill="var(--mute)">{_money(t)}</text>')
    for i, q in enumerate(quarters):
        cx = LEFT + slot * (i + 0.5)
        beat = q.beat
        colour = "var(--grey)" if beat is None else "var(--green)" if beat else "var(--red)"
        detail = (f"Real {_money(q.actual)}" + (f" · estimación {_money(q.estimate)}" if q.estimate is not None else " · sin estimación")
                  if q.actual is not None else "")
        verdict = "" if beat is None else (" · alcanzó o superó la estimación" if beat else " · por debajo de la estimación")
        tip = escape(f"{_money(q.actual)}|BPA {q.day.strftime('%d/%m/%Y')}|{detail}{verdict}".replace('"', "'"), quote=True)
        parts.append(f'<g tabindex="0" data-tip="{tip}"><rect x="{cx - slot / 2:.1f}" y="{TOP}" width="{slot:.1f}" height="{plot_h}" fill="transparent"/>')
        if q.estimate is not None:
            parts.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(q.estimate):.1f}" y2="{y(q.actual):.1f}" stroke="{colour}" stroke-width="1.5" opacity=".45"/>'
                         f'<circle cx="{cx:.1f}" cy="{y(q.estimate):.1f}" r="5" fill="var(--panel)" stroke="var(--mute)" stroke-width="1.5"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{y(q.actual):.1f}" r="5.5" fill="{colour}"/></g>'
                     f'<text x="{cx:.1f}" y="{HEIGHT - 6}" text-anchor="middle" font-size="10" fill="var(--mute)">{q.day.strftime("%m/%y")}</text>')
    parts.append("</svg>")
    return "".join(parts)


def dividend_line(dividend: Optional[Dividend], today: date) -> str:
    """«Dividendo: 2,2 % anual · 0,53 $ por pago · ex 01/12/2026 (próximo) · pago 15/12/2026»; vacío si no paga."""
    if dividend is None or dividend.empty:
        return ""
    bits = []
    if dividend.yield_pct:
        bits.append(f"<b>{_money(dividend.yield_pct)} %</b> anual")
    if dividend.per_payment:
        bits.append(f"{_money(dividend.per_payment)} $ por pago")
    if dividend.ex_date:
        bits.append(f"ex {dividend.ex_date.strftime('%d/%m/%Y')} ({'próximo' if dividend.ex_date >= today else 'último'})")
    if dividend.pay_date:
        bits.append(f"pago {dividend.pay_date.strftime('%d/%m/%Y')}")
    return f'<div style="margin-top:6px"><b>Dividendo</b> <span class="muted">· {" · ".join(bits)}</span></div>'


def history_html(quarters: Sequence[EarningsQuarter], dividend: Optional[Dividend], today: date) -> str:
    """Resultados de los últimos trimestres (con «n/m» de veces que se alcanzó la estimación) y dividendo, para la ficha de los gráficos."""
    out = []
    if quarters:
        summary = beat_summary(quarters)
        badge = ""
        if summary:
            beats, total = summary
            ratio = beats / total
            colour = "var(--green)" if ratio >= 0.75 else "var(--amber)" if ratio >= 0.5 else "var(--red)"
            badge = (f' <span style="border:1px solid {colour};color:{colour};border-radius:6px;padding:0 6px;font-weight:700" '
                     f'title="Trimestres en que el BPA real alcanzó o superó la estimación de los analistas">{beats}/{total}</span>')
        out.append(f'<div style="margin-top:6px"><b>Resultados por acción (BPA)</b>{badge} '
                   '<span class="muted">· relleno = real, hueco = estimación · últimos '
                   f'{len(quarters)} trimestres</span></div>{earnings_svg(quarters)}')
    out.append(dividend_line(dividend, today))
    body = "".join(out)
    return f'<div class="co-history" style="margin:0 0 8px;font-size:12.5px;line-height:1.4">{body}</div>' if body else ""
