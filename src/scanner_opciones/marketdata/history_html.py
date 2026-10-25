"""Fragmento HTML de la ficha de los gráficos con el historial de resultados (BPA real frente a estimación) y el dividendo. Funciones puras: todo texto
se escapa. Sin librerías: un SVG pequeño con un punto relleno por trimestre (real) y uno hueco (estimación)."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Optional, Sequence

from scanner_opciones.marketdata.history import AnnualYear, Dividend, EarningsQuarter, annual_growth, beat_summary, eps_growth

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
    parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(0.0):.1f}" y2="{y(0.0):.1f}" stroke="var(--mute)" stroke-width="1.5"/>')
    if lo < 0:
        parts.append(f'<text x="{LEFT - 5}" y="{y(0.0) + 3.5:.1f}" text-anchor="end" font-size="10" font-weight="600" fill="var(--text)">0</text>')
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


def _signed(value: float, decimals: int = 0) -> str:
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):.{decimals}f}".replace(".", ",") + " %"


def _tone(value: Optional[float]) -> str:
    return "var(--mute)" if value is None else "var(--green)" if value > 0 else "var(--red)" if value < 0 else "var(--mute)"


def _billions(value: float) -> str:
    return f"{value / 1e9:.2f}".replace(".", ",") + " B"


def annual_svg(years: Sequence[AnnualYear]) -> str:
    """Barras de ingresos y de beneficio neto por año fiscal (eje derecho, en miles de millones de $) y línea del margen neto (eje izquierdo, %)."""
    bars = [v for y in years for v in (y.revenue, y.net_income) if v is not None]
    margins = [m for m in (y.net_margin_pct for y in years) if m is not None]
    if not bars:
        return ""
    b_hi, b_lo = max(max(bars), 0.0), min(min(bars), 0.0)
    if b_hi <= b_lo:
        b_hi = b_lo + 1.0
    m_hi, m_lo = (max(max(margins), 0.0), min(min(margins), 0.0)) if margins else (1.0, 0.0)
    if m_hi <= m_lo:
        m_hi = m_lo + 1.0
    # El 0 de los dos ejes (miles de millones y %) cae a la misma altura: así una sola línea de 0 vale para las barras y para el margen.
    frac0 = -b_lo / (b_hi - b_lo)
    if frac0 > 0:
        if -m_lo / (m_hi - m_lo) < frac0:
            m_lo = -frac0 * m_hi / (1 - frac0) if frac0 < 1 else m_lo
        else:
            m_hi = -m_lo * (1 - frac0) / frac0
    left, right = 40, 48
    height = HEIGHT + 26         # dos filas más bajo el año: variación interanual de ingresos y de beneficio neto
    plot_w, plot_h = WIDTH - left - right, HEIGHT - TOP - BOTTOM
    growth = annual_growth(years)

    def yb(v: float) -> float:
        return TOP + plot_h * (1 - (v - b_lo) / (b_hi - b_lo))

    def ym(v: float) -> float:
        return TOP + plot_h * (1 - (v - m_lo) / (m_hi - m_lo))

    slot = plot_w / len(years)
    bw = min(14.0, slot / 4)
    parts = [f'<svg viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" role="img" '
             'aria-label="Ingresos, beneficio neto y margen neto por año fiscal, con su variación interanual" style="max-width:100%;height:auto">']
    for i in range(3):
        frac = i / 2
        gy = TOP + plot_h * (1 - frac)
        parts.append(f'<line x1="{left}" x2="{WIDTH - right}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="var(--line)" stroke-width="1"/>'
                     f'<text x="{left - 5}" y="{gy + 3.5:.1f}" text-anchor="end" font-size="10" fill="var(--mute)">{m_lo + (m_hi - m_lo) * frac:.0f} %</text>'
                     f'<text x="{WIDTH - right + 5}" y="{gy + 3.5:.1f}" font-size="10" fill="var(--mute)">{_billions(b_lo + (b_hi - b_lo) * frac)}</text>')
    zero = yb(0.0)
    if b_lo < 0:
        parts.append(f'<line x1="{left}" x2="{WIDTH - right}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="var(--mute)" stroke-width="1.5"/>'
                     f'<text x="{left - 5}" y="{zero + 3.5:.1f}" text-anchor="end" font-size="10" font-weight="600" fill="var(--text)">0 %</text>'
                     f'<text x="{WIDTH - right + 5}" y="{zero + 3.5:.1f}" font-size="10" font-weight="600" fill="var(--text)">0</text>')
    else:
        parts.append(f'<line x1="{left}" x2="{WIDTH - right}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="var(--mute)" stroke-width="1.5"/>')
    points = []
    for i, y in enumerate(years):
        cx = left + slot * (i + 0.5)
        margin = y.net_margin_pct
        detail = " · ".join(filter(None, [
            f"ingresos {_billions(y.revenue)} $" if y.revenue is not None else "",
            f"beneficio neto {_billions(y.net_income)} $" if y.net_income is not None else "",
            f"margen neto {_money(margin)} %" if margin is not None else "",
            f"ingresos {_signed(growth[i][0], 1)} interanual" if growth[i][0] is not None else "",
            f"beneficio neto {_signed(growth[i][1], 1)} interanual" if growth[i][1] is not None else ""]))
        tip = escape(f"{y.end.year}|Año fiscal {y.end.strftime('%d/%m/%Y')}|{detail}".replace('"', "'"), quote=True)
        parts.append(f'<g tabindex="0" data-tip="{tip}"><rect x="{cx - slot / 2:.1f}" y="{TOP}" width="{slot:.1f}" height="{plot_h}" fill="transparent"/>')
        for k, (value, colour) in enumerate(((y.revenue, "#4f7cff"), (y.net_income, "#3ec9d6"))):
            if value is not None:
                x, top = cx - bw - 1 + k * (bw + 2), yb(value)
                parts.append(f'<rect x="{x:.1f}" y="{min(top, zero):.1f}" width="{bw:.1f}" height="{max(abs(zero - top), 1.0):.1f}" rx="2" fill="{colour}"/>')
        parts.append("</g>")
        if margin is not None:
            points.append((cx, ym(margin)))
        parts.append(f'<text x="{cx:.1f}" y="{HEIGHT - 6}" text-anchor="middle" font-size="10" fill="var(--mute)">{y.end.strftime("%y")}</text>')
        for row, (label, value) in enumerate((("Ing", growth[i][0]), ("BN", growth[i][1]))):
            text = "—" if value is None else _signed(value)
            parts.append(f'<text x="{cx:.1f}" y="{HEIGHT + 8 + row * 12}" text-anchor="middle" font-size="9.5" fill="{_tone(value)}">{label} {text}</text>')
    if len(points) > 1:
        parts.append('<polyline fill="none" stroke="#f08a24" stroke-width="2" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in points) + '"/>')
    parts.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="var(--panel)" stroke="#f08a24" stroke-width="1.5"/>' for x, y in points)
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


def history_html(quarters: Sequence[EarningsQuarter], dividend: Optional[Dividend], today: date, annual: Sequence[AnnualYear] = ()) -> str:
    """Resultados anuales (ingresos, beneficio neto y margen), de los últimos trimestres (con «n/m» de veces que se alcanzó la estimación) y
    dividendo, para la ficha de los gráficos."""
    out = []
    chart = annual_svg(annual) if annual else ""
    if chart:
        out.append(f'<div style="margin-top:6px"><b>Resultados anuales</b> <span class="muted">· <span style="color:#4f7cff">■</span> ingresos '
                   f'<span style="color:#3ec9d6">■</span> beneficio neto <span style="color:#f08a24">●</span> margen neto (%) · {escape(annual[-1].source)}, '
                   f'últimos {len(annual)} años fiscales · bajo cada año, variación interanual (YoY) de ingresos (Ing) y beneficio neto (BN)</span></div>{chart}')
    if quarters:
        summary = beat_summary(quarters)
        badge = ""
        if summary:
            beats, total = summary
            ratio = beats / total
            colour = "var(--green)" if ratio >= 0.75 else "var(--amber)" if ratio >= 0.5 else "var(--red)"
            badge = (f' <span style="border:1px solid {colour};color:{colour};border-radius:6px;padding:0 6px;font-weight:700" '
                     f'title="Trimestres en que el BPA real alcanzó o superó la estimación de los analistas">{beats}/{total}</span>')
        yoy = eps_growth(quarters)
        if yoy is not None:
            g, before, last = yoy
            badge += (f' <span style="color:{_tone(g)};font-weight:700" title="BPA real de {last.day.strftime("%m/%Y")} ({_money(last.actual)}) frente al '
                      f'de {before.day.strftime("%m/%Y")} ({_money(before.actual)})">{_signed(g, 1)} interanual</span>')
        out.append(f'<div style="margin-top:6px"><b>Resultados por acción (BPA)</b>{badge} '
                   '<span class="muted">· relleno = real, hueco = estimación · últimos '
                   f'{len(quarters)} trimestres</span></div>{earnings_svg(quarters)}')
    out.append(dividend_line(dividend, today))
    body = "".join(out)
    return f'<div class="co-history" style="margin:0 0 8px;font-size:12.5px;line-height:1.4">{body}</div>' if body else ""
