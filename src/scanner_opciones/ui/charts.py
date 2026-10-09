"""Gráfico SVG del historial de cierres mensuales frente al strike de una put (pestaña Scanner).

Sin librerías: la curva, los ejes y las etiquetas se dibujan a mano. Lo único que entra en el SVG son números y
fechas calculados aquí; el ticker se escapa.
"""
from __future__ import annotations

from datetime import date
from html import escape

from scanner_opciones.metrics.technical import StrikeHistory

W, H = 760, 300
LEFT, RIGHT, TOP, BOTTOM = 58, 22, 44, 34
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
BLUE, RED, YELLOW, ORANGE = "#2f6fd1", "#d64545", "#d9a400", "#e87a1e"


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _mon(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} '{d.year % 100:02d}"


def _smooth(pts: list[tuple[float, float]]) -> str:
    """Trazo suave (Catmull-Rom convertido a Bézier) por los puntos."""
    if len(pts) < 3:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d


def _series(h: StrikeHistory) -> list[tuple[date, float]]:
    """Cierres mensuales más el último toque (cierre diario), para que la curva pase por el aro naranja."""
    pts = list(h.months)
    if h.touch and h.touch[0] >= pts[0][0] and all(d != h.touch[0] for d, _ in pts):
        pts.append((h.touch[0], h.touch[1]))
        pts.sort(key=lambda t: t[0])
    return pts


def _pill(x: float, y: float, text: str, fill: str, anchor: str = "middle") -> str:
    w = 7.2 * len(text) + 18
    left = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
    return (f'<rect x="{left:.1f}" y="{y - 11:.1f}" width="{w:.1f}" height="22" rx="11" fill="{fill}"/>'
            f'<text x="{left + w / 2:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="11.5" font-weight="700" fill="#fff">{escape(text)}</text>')


def strike_chart_html(h: StrikeHistory, ticker: str, strike: float, near_pct: float) -> str:
    """Resumen + gráfico (fragmento HTML). Con menos de 2 cierres mensuales devuelve solo un aviso."""
    if len(h.months) < 2:
        return '<p class="muted">Sin histórico de cierres suficiente para dibujar el gráfico.</p>'
    n = len(h.months)
    current = h.months[-1][1]
    closest = h.closest
    pct_closest = (closest[1] - strike) / strike * 100
    ok = h.above == n
    summary = (f'<div class="sh-banner {"ok" if ok else "warn"}"><b>{"✓" if ok else "⚠"} {h.above} de {n} meses por encima del strike '
               f'{_usd(strike)}</b> · Más cercano: {_usd(closest[1])} ({_mon(closest[0])}, '
               f'{abs(pct_closest):.1f}% {"por encima" if pct_closest >= 0 else "por debajo"})</div>')
    if h.touch:
        d, px, days = h.touch
        unit = "hoy" if days == 0 else ("hace 1 día" if days == 1 else f"hace {days} días")
        touch_line = (f'<div class="sh-touch"><b>Último toque del strike: {unit}</b> · cierre de {_usd(px)} el '
                      f'{d.day} {_MONTHS[d.month - 1]} {d.year} (cierre ≤ strike)</div>')
    else:
        touch_line = (f'<div class="sh-touch"><b>Sin toques del strike</b> · ningún cierre en o por debajo de {_usd(strike)} '
                      f'en los {h.history_days} días de histórico</div>')

    # escalas
    lo = min(strike, min(p for _, p in h.months), h.touch[1] if h.touch else strike)
    hi = max(strike, max(p for _, p in h.months))
    pad = (hi - lo) * 0.1 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    t0, t1 = h.months[0][0].toordinal(), h.months[-1][0].toordinal()
    span = max(t1 - t0, 1)
    px_x = lambda d: LEFT + (d.toordinal() - t0) / span * (W - LEFT - RIGHT)
    px_y = lambda v: TOP + (hi - v) / (hi - lo) * (H - TOP - BOTTOM)
    pts = [(px_x(d), px_y(p)) for d, p in h.months]
    curve = [(px_x(d), px_y(p)) for d, p in _series(h)]
    base_y = H - BOTTOM
    sy = px_y(strike)

    g = []
    for i in range(5):                                                 # cuadrícula y precios del eje
        v = lo + (hi - lo) * i / 4
        y = px_y(v)
        g.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="currentColor" opacity=".12"/>'
                 f'<text x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">${v:,.0f}</text>')
    step = max(1, round(n / 12))                                       # etiquetas de meses en el eje X
    for i in range(0, n, step):
        g.append(f'<text x="{pts[i][0]:.1f}" y="{H - 10}" text-anchor="middle" font-size="11" fill="currentColor" opacity=".6">{_mon(h.months[i][0])}</text>')
    line = _smooth(curve)
    g.append(f'<path d="{line} L{curve[-1][0]:.1f},{base_y} L{curve[0][0]:.1f},{base_y} Z" fill="{BLUE}" opacity=".10"/>')
    g.append(f'<path d="{line}" fill="none" stroke="{BLUE}" stroke-width="2.2"/>')
    g.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{sy:.1f}" y2="{sy:.1f}" stroke="{RED}" stroke-width="1.6" stroke-dasharray="6 4"/>')
    g.append(f'<text x="{LEFT - 8}" y="{sy + 4:.1f}" text-anchor="end" font-size="11" font-weight="700" fill="{RED}">${strike:,.0f}</text>')
    for (d, p), (x, y) in zip(h.months, pts):                          # puntos: rojo ≤ strike, amarillo cerca, azul resto
        colour = RED if p <= strike else (YELLOW if p <= strike * (1 + near_pct / 100) else BLUE)
        g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{colour}"><title>{_mon(d)}: {_usd(p)}</title></circle>')
    g.append(_pill(LEFT + (W - LEFT - RIGHT) * 0.5, sy, f"STRIKE {_usd(strike)}", RED))
    if h.touch:                                                        # último toque: aro naranja con su etiqueta
        d, p, days = h.touch
        if d.toordinal() >= t0:
            x, y = px_x(d), px_y(p)
            g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="none" stroke="{ORANGE}" stroke-width="2.2"/>')
            label = f"{_usd(p)} · {d.day} {_MONTHS[d.month - 1]} · hace {days} d"
            ty = y + 28 if y + 28 < base_y - 4 else y - 18      # debajo del aro (arriba suele estar el precio actual)
            anchor = "end" if x > W * 0.6 else "start"
            tx = x - 4 if anchor == "end" else x + 4
            g.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" font-size="11.5" font-weight="700" fill="{ORANGE}">{escape(label)}</text>')
    g.append(_pill(pts[-1][0], max(pts[-1][1] - 20, 14), f"ACTUAL {_usd(current)}", BLUE, anchor="end"))

    legend = (f'<div class="sh-legend"><span style="--c:{BLUE}">Cierre mensual</span><span style="--c:{RED}" class="dash">Strike {_usd(strike)}</span>'
              f'<span style="--c:{YELLOW}">A menos de {near_pct:g}% del strike</span><span style="--c:{RED}">En o bajo el strike</span>'
              f'<span style="--c:{ORANGE}" class="ring">Último toque (cierre diario)</span></div>')
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cierres mensuales de {escape(ticker)} frente al strike" '
           f'class="sh-svg">{"".join(g)}</svg>')
    return f'<div class="sh-wrap">{summary}{touch_line}{legend}{svg}</div>'


MW, MH = 110, 30


def strike_mini_svg(h: StrikeHistory, strike: float, near_pct: float) -> str:
    """Miniatura (imagen SVG independiente, sin `currentColor`) del gráfico del strike para cada fila de la tabla:
    curva de cierres mensuales, strike punteado, último punto (con el color de su cierre) y aro naranja del último toque."""
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MW} {MH}" width="{MW}" height="{MH}">'
    if len(h.months) < 2:
        return head + f'<text x="{MW / 2}" y="{MH / 2 + 4}" text-anchor="middle" font-size="11" fill="#8b94a3">—</text></svg>'
    lo = min(strike, min(p for _, p in h.months), h.touch[1] if h.touch else strike)
    hi = max(strike, max(p for _, p in h.months))
    pad = (hi - lo) * 0.12 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    t0 = h.months[0][0].toordinal()
    span = max(h.months[-1][0].toordinal() - t0, 1)
    x = lambda d: 3 + (d.toordinal() - t0) / span * (MW - 6)
    y = lambda v: 3 + (hi - v) / (hi - lo) * (MH - 6)
    pts = [(x(d), y(p)) for d, p in h.months]
    sy = y(strike)
    last = h.months[-1][1]
    colour = RED if last <= strike else (YELLOW if last <= strike * (1 + near_pct / 100) else BLUE)
    curve = [(x(d), y(p)) for d, p in _series(h)]
    parts = [f'<path d="{_smooth(curve)} L{curve[-1][0]:.1f},{MH - 2} L{curve[0][0]:.1f},{MH - 2} Z" fill="{BLUE}" opacity=".12"/>',
             f'<path d="{_smooth(curve)}" fill="none" stroke="{BLUE}" stroke-width="1.5"/>',
             f'<line x1="2" x2="{MW - 2}" y1="{sy:.1f}" y2="{sy:.1f}" stroke="{RED}" stroke-width="1" stroke-dasharray="3 2"/>',
             f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="{colour}"/>']
    if h.touch and h.touch[0].toordinal() >= t0:
        parts.append(f'<circle cx="{x(h.touch[0]):.1f}" cy="{y(h.touch[1]):.1f}" r="3.6" fill="none" stroke="{ORANGE}" stroke-width="1.4"/>')
    return head + "".join(parts) + "</svg>"
