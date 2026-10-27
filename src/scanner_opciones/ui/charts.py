"""Gráfico SVG del historial de cierres mensuales frente al strike de una put (pestaña Scanner).

Sin librerías: la curva, los ejes y las etiquetas se dibujan a mano. Lo único que entra en el SVG son números y
fechas calculados aquí; el ticker se escapa.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from html import escape

from scanner_opciones.metrics.technical import StrikeHistory

W, H = 760, 300
LEFT, RIGHT, TOP, BOTTOM = 58, 84, 20, 34
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
BLUE, RED, YELLOW, ORANGE = "#2f6fd1", "#d64545", "#d9a400", "#e87a1e"
MAX_EARNINGS_AHEAD = 120          # días: unos resultados más lejanos no alargan el eje
MA50, MA200 = "#8a5cd0", "#1f9d8a"
LONG = "#2f7de1"                  # put comprada de un spread (la vendida va en rojo)


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _mon3(d: date) -> str:
    """Vencimiento: mes de 3 letras y día («dic 18»)."""
    return f"{_MONTHS[d.month - 1]} {d.day}"


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
    w = 6.4 * len(text) + 12
    left = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
    return (f'<rect x="{left:.1f}" y="{y - 8:.1f}" width="{w:.1f}" height="16" rx="8" fill="{fill}"/>'
            f'<text x="{left + w / 2:.1f}" y="{y + 3.5:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#fff">{escape(text)}</text>')


def _side_tags(tags: list[tuple[float, str, str, str]], x: float, top: float, bottom: float) -> str:
    """Etiquetas de valor en el margen derecho (fuera de la zona de datos), separadas entre sí para que no se pisen.
    Cada una es (y, texto, color, clase CSS de la media o '')."""
    gap = 17.0
    tags = sorted(tags, key=lambda t: t[0])
    ys = [t[0] for t in tags]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    over = ys[-1] - bottom if ys else 0
    if over > 0:
        ys = [y - over for y in ys]
        for i in range(len(ys) - 2, -1, -1):
            ys[i] = min(ys[i], ys[i + 1] - gap)
    out = []
    for (y0, text, colour, cls), y in zip(tags, ys):
        y = max(y, top)
        pill = _pill(x, y, text, colour, anchor="start")
        lead = f'<line x1="{x - 6:.1f}" x2="{x:.1f}" y1="{y0:.1f}" y2="{y:.1f}" stroke="{colour}" stroke-width="1" opacity=".6"/>'
        out.append(f'<g class="{cls}">{lead}{pill}</g>' if cls else lead + pill)
    return "".join(out)


def strike_chart_html(h: StrikeHistory, ticker: str, strike: float, near_pct: float, earnings: Optional[date] = None,
                      long_strike: Optional[float] = None, expiry: Optional[date] = None) -> str:
    """Resumen + gráfico (fragmento HTML). Con menos de 2 cierres mensuales devuelve solo un aviso.
    `long_strike`: strike de la put comprada si es un spread; se dibuja en azul (el vendido, `strike`, en rojo).
    `expiry`: vencimiento del contrato; se escribe («dic 18») bajo el precio de cada strike en el eje."""
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

    mas = [(label, key, colour, series) for label, key, colour, series in
           (("SMA50", "ma50", MA50, h.sma50), ("SMA200", "ma200", MA200, h.sma200)) if len(series) >= 2]
    ma_bits = [f'{label}: {_usd(s[-1][1])} (precio {(current - s[-1][1]) / s[-1][1] * 100:+.1f}%)' for label, _, _, s in mas]
    ma_line = f'<div class="sh-touch"><b>Medias</b> · {" · ".join(ma_bits)}</div>' if ma_bits else ""

    # escalas
    lo = min(strike, min(p for _, p in h.months), h.touch[1] if h.touch else strike)
    hi = max(strike, max(p for _, p in h.months))
    if long_strike is not None:
        lo, hi = min(lo, long_strike), max(hi, long_strike)
    for _, _, _, s in mas:
        lo, hi = min(lo, min(v for _, v in s)), max(hi, max(v for _, v in s))
    pad = (hi - lo) * 0.1 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    t0, t1 = h.months[0][0].toordinal(), h.months[-1][0].toordinal()
    if earnings is not None and earnings.toordinal() > t1 and (earnings - h.months[-1][0]).days <= MAX_EARNINGS_AHEAD:
        t1 = earnings.toordinal()          # el eje se alarga hasta los resultados próximos
    else:
        earnings = None
    span = max(t1 - t0, 1)
    px_x = lambda d: LEFT + (d.toordinal() - t0) / span * (W - LEFT - RIGHT)
    px_y = lambda v: TOP + (hi - v) / (hi - lo) * (H - TOP - BOTTOM)
    pts = [(px_x(d), px_y(p)) for d, p in h.months]
    curve = [(px_x(d), px_y(p)) for d, p in _series(h)]
    base_y = H - BOTTOM
    sy = px_y(strike)

    g = []
    tags: list[tuple[float, str, str, str]] = []
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
    g.append(f'<path class="sh-curve" d="{line}" fill="none" stroke="{BLUE}" stroke-width="2.2"/>')
    for label, key, colour, s in mas:                                  # medias móviles diarias (activables desde la leyenda)
        mp = [(px_x(d), px_y(v)) for d, v in s]
        g.append(f'<g class="sh-ma {key}"><path d="{_smooth(mp)}" fill="none" stroke="{colour}" stroke-width="1.4" opacity=".9"/></g>')
        tags.append((mp[-1][1], _usd(s[-1][1]), colour, f"sh-ma {key}"))
    g.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{sy:.1f}" y2="{sy:.1f}" stroke="{RED}" stroke-width="1.6" stroke-dasharray="6 4"/>')
    g.append(f'<text x="{LEFT - 8}" y="{sy + 4:.1f}" text-anchor="end" font-size="11" font-weight="700" fill="{RED}">${strike:,.0f}</text>')
    if expiry is not None:
        g.append(f'<text x="{LEFT - 8}" y="{sy + 16:.1f}" text-anchor="end" font-size="10" fill="{RED}">{_mon3(expiry)}</text>')
    for (d, p), (x, y) in zip(h.months, pts):                          # puntos: rojo ≤ strike, amarillo cerca, azul resto
        colour = RED if p <= strike else (YELLOW if p <= strike * (1 + near_pct / 100) else BLUE)
        g.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{colour}"><title>{_mon(d)}: {_usd(p)}</title></circle>')
    tags.append((sy, _usd(strike), RED, ""))
    if long_strike is not None:                                        # put comprada del spread, en azul
        ly = px_y(long_strike)
        g.append(f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{ly:.1f}" y2="{ly:.1f}" stroke="{LONG}" stroke-width="1.6" stroke-dasharray="6 4"/>')
        g.append(f'<text x="{LEFT - 8}" y="{ly + 4:.1f}" text-anchor="end" font-size="11" font-weight="700" fill="{LONG}">${long_strike:,.0f}</text>')
        if expiry is not None:
            g.append(f'<text x="{LEFT - 8}" y="{ly + 16:.1f}" text-anchor="end" font-size="10" fill="{LONG}">{_mon3(expiry)}</text>')
        tags.append((ly, _usd(long_strike), LONG, ""))
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
    if earnings is not None:                                           # raya vertical en la fecha de resultados
        x = px_x(earnings)
        days = (earnings - h.months[-1][0]).days
        g.append(f'<g><title>Resultados el {earnings.day} {_MONTHS[earnings.month - 1]} {earnings.year}</title>'
                 f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{base_y}" stroke="{ORANGE}" stroke-width="1.8" stroke-dasharray="2 3"/>'
                 f'<text x="{x - 4:.1f}" y="{TOP + 10}" text-anchor="end" font-size="11" font-weight="700" fill="{ORANGE}" stroke="var(--panel)" '
                 f'stroke-width="3" paint-order="stroke">resultados {earnings.day} {_MONTHS[earnings.month - 1]} · en {days} d</text></g>')
    tags.append((pts[-1][1], _usd(current), BLUE, ""))
    g.append(_side_tags(tags, W - RIGHT + 12, TOP, base_y))

    sold_label = "Strike vendido" if long_strike is not None else "Strike"
    legend = (f'<div class="sh-legend"><span style="--c:{BLUE}">Cierre mensual (actual)</span><span style="--c:{RED}" class="dash">{sold_label} {_usd(strike)}</span>'
              + (f'<span style="--c:{LONG}" class="dash">Comprada {_usd(long_strike)}</span>' if long_strike is not None else "")
              + f'<span style="--c:{YELLOW}">A menos de {near_pct:g}% del strike</span><span style="--c:{RED}">En o bajo el strike</span>'
              f'<span style="--c:{ORANGE}" class="ring">Último toque (cierre diario)</span>'
              + (f'<span style="--c:{ORANGE}" class="dash">Resultados {earnings.day} {_MONTHS[earnings.month - 1]}</span>' if earnings is not None else "")
              + "".join(f'<label class="sh-tog" style="--c:{c}"><input type="checkbox" data-ma="{k}" checked> {lb}</label>' for lb, k, c, _ in mas)
              + '</div>')
    scale = f'data-lo="{lo:.4f}" data-hi="{hi:.4f}" data-top="{TOP}" data-base="{base_y}" data-t0="{t0}" data-t1="{t1}" data-left="{LEFT}" data-right="{W - RIGHT}"'
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cierres mensuales de {escape(ticker)} frente al strike" '
           f'class="sh-svg" {scale}>{"".join(g)}</svg>')
    return f'<div class="sh-wrap">{summary}{touch_line}{ma_line}{legend}{svg}</div>'


MW, MH = 110, 30


def strike_mini_svg(h: StrikeHistory, strike: float, near_pct: float, long_strike: Optional[float] = None) -> str:
    """Miniatura (imagen SVG independiente, sin `currentColor`) del gráfico del strike para cada fila de la tabla:
    curva de cierres mensuales, strike punteado, último punto (con el color de su cierre) y aro naranja del último toque."""
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MW} {MH}" width="{MW}" height="{MH}">'
    if len(h.months) < 2:
        return head + f'<text x="{MW / 2}" y="{MH / 2 + 4}" text-anchor="middle" font-size="11" fill="#8b94a3">—</text></svg>'
    lo = min(strike, min(p for _, p in h.months), h.touch[1] if h.touch else strike)
    hi = max(strike, max(p for _, p in h.months))
    if long_strike is not None:
        lo, hi = min(lo, long_strike), max(hi, long_strike)
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
    if long_strike is not None:
        parts.insert(2, f'<line x1="2" x2="{MW - 2}" y1="{y(long_strike):.1f}" y2="{y(long_strike):.1f}" stroke="{LONG}" stroke-width="1" stroke-dasharray="3 2"/>')
    if h.touch and h.touch[0].toordinal() >= t0:
        parts.append(f'<circle cx="{x(h.touch[0]):.1f}" cy="{y(h.touch[1]):.1f}" r="3.6" fill="none" stroke="{ORANGE}" stroke-width="1.4"/>')
    return head + "".join(parts) + "</svg>"
