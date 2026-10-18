"""Gráfico por ticker de las puts vendidas del Panel: cierres diarios de las últimas semanas, el strike (o strikes) como línea horizontal,
el vencimiento como línea vertical y, la fecha en la que toca revisarla (`management.review_dte`,
21 DTE) con los días que faltan. Con TPC (mantener hasta el vencimiento) no se revisa, pero la fecha de 21 DTE se dibuja igual como referencia. HTML/SVG generado en el servidor, sin librerías.
Todo texto que viene de los datos se escapa."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from markupsafe import Markup

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.portfolio.management import TASTYTRADE, PutStatus
from scanner_opciones.ui.viz import _e, _scale, empty, nice_ticks

W, H = 560, 250
LEFT, RIGHT, TOP, BOTTOM = 46, 16, 30, 28
FUTURE_DAYS = 60            # hasta dónde se dibuja el futuro; un vencimiento más lejano se marca con una flecha en el borde derecho
SOLD, BOUGHT, EXPIRY, REVIEW = "var(--red)", "var(--grey)", "var(--ink)", "var(--amber)"


@dataclass(frozen=True)
class Exit:
    """Qué toca hacer con una put según su perfil de gestión (para la leyenda bajo el gráfico)."""
    text: str
    review_date: Optional[date] = None
    days_to_review: Optional[int] = None
    is_review: bool = False              # True: se gestiona con Tastytrade y toca revisarla; False (TPC): la fecha es solo una referencia


def exit_plan(s: PutStatus, cfg: ManagementSettings, today: date) -> Exit:
    """Estrategia de salida: Tastytrade recoge al `take_profit_pct` de la prima y revisa a `review_dte` DTE; TPC mantiene hasta el vencimiento."""
    review = s.expiry - timedelta(days=cfg.review_dte)
    left = (review - today).days
    if s.profile != TASTYTRADE:       # TPC no revisa: la fecha es solo una referencia
        ref = f"referencia a {cfg.review_dte} DTE el {review:%d/%m} (faltan {left} días)" if left > 0 else f"ya dentro de los {cfg.review_dte} DTE"
        return Exit(f"TPC: mantener hasta el vencimiento ({ref})", review, left, False)
    if s.captured_pct is not None and s.captured_pct >= cfg.take_profit_pct:
        return Exit(f"objetivo del {cfg.take_profit_pct:g} % alcanzado ({s.captured_pct:.0f} %): recoger beneficios", review, left, True)
    if left > 0:
        return Exit(f"revisar el {review:%d/%m} (a {cfg.review_dte} DTE): faltan {left} días", review, left, True)
    return Exit(f"en zona de revisión (≤ {cfg.review_dte} DTE): cerrar o rolar si no se ha llegado al {cfg.take_profit_pct:g} %", review, left, True)


def position_chart(closes: Sequence[tuple[date, float]], puts: Sequence[PutStatus], cfg: ManagementSettings, today: date, weeks: int = 10,
                   price: Optional[float] = None) -> Markup:
    """`closes`: cierres diarios (fecha, cierre) del ticker; `puts`: sus puts vendidas (un spread es una fila con `long_strike`)."""
    start = today - timedelta(weeks=weeks)
    bars = sorted((d, p) for d, p in closes if start <= d <= today)
    if len(bars) < 2:
        return empty("Sin cierres diarios guardados de este ticker")
    furthest = max(s.expiry for s in puts)
    end = max(min(furthest, today + timedelta(days=FUTURE_DAYS)), today + timedelta(days=7))
    span = max((end - start).days, 1)
    px = lambda d: _scale((d - start).days, 0, span, LEFT, W - RIGHT)           # noqa: E731

    strikes: dict[tuple[float, str], list[str]] = {}
    for s in puts:
        strikes.setdefault((s.strike, "sold"), []).append(f"{s.contracts} × vencimiento {s.expiry:%d/%m}")
        if s.long_strike is not None:
            strikes.setdefault((s.long_strike, "bought"), []).append(f"cobertura del spread {s.strike:g}/{s.long_strike:g}")
    values = [p for _, p in bars] + [k for k, _ in strikes] + ([price] if price else [])
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.10 or hi * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    py = lambda v: _scale(v, lo, hi, H - BOTTOM, TOP)                           # noqa: E731

    out = [f'<svg class="lc" viewBox="0 0 {W} {H}" role="img" aria-label="Evolución diaria, strikes y vencimientos">']
    for v in nice_ticks(lo, hi, 4):                                                    # cuadrícula y precios
        if lo <= v <= hi:
            out.append(f'<line class="gl" x1="{LEFT}" x2="{W - RIGHT}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
                       f'<text class="ax" x="{LEFT - 6}" y="{py(v) + 4:.1f}" text-anchor="end">{_e(f"{v:g}")}</text>')
    step = 14 if span > 70 else 7                                                       # fechas del eje
    d = start
    while d <= end:
        out.append(f'<text class="ax" x="{px(d):.1f}" y="{H - 8}" text-anchor="middle">{d:%d/%m}</text>')
        d += timedelta(days=step)

    reviews = {}                                                                       # zona de revisión: de la fecha de revisión al vencimiento
    for s in puts:
        plan = exit_plan(s, cfg, today)
        if plan.review_date is not None:
            reviews[(plan.review_date, s.expiry)] = plan
    for (rd, exp), plan in reviews.items():
        x0, x1 = px(max(rd, start)), px(min(exp, end))
        if x1 > x0:
            out.append(f'<rect x="{x0:.1f}" y="{TOP}" width="{x1 - x0:.1f}" height="{H - BOTTOM - TOP}" fill="{REVIEW}" opacity=".12"/>')

    for (k, kind), notes in sorted(strikes.items()):                                   # strikes: vendido (rojo) y comprado (gris)
        y, color = py(k), SOLD if kind == "sold" else BOUGHT
        label = f"strike {k:g}" if kind == "sold" else f"comprada {k:g}"
        tip = f"{k:g}|{'Strike vendido' if kind == 'sold' else 'Put comprada'}|{'; '.join(notes)}"
        dash = "" if kind == "sold" else ' stroke-dasharray="5 4"'
        out.append(f'<g tabindex="0" data-tip="{_e(tip)}"><line x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="1.6"{dash}/><line x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="transparent" stroke-width="10"/>'
                   f'<text x="{LEFT + 4}" y="{y - 4:.1f}" font-size="11" font-weight="700" fill="{color}" stroke="var(--panel)" stroke-width="3" paint-order="stroke">{_e(label)}</text></g>')

    pts = [(px(d), py(p)) for d, p in bars]                                            # cierres diarios
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{path} L{pts[-1][0]:.1f},{H - BOTTOM} L{pts[0][0]:.1f},{H - BOTTOM} Z"
    out.append(f'<path class="ar" d="{area}"/><path class="ln" d="{path}"/>')
    last_price = price if price else bars[-1][1]
    out.append(f'<circle class="mk" cx="{pts[-1][0]:.1f}" cy="{py(last_price):.1f}" r="4"/>')
    for i, ((x, y), (d, p)) in enumerate(zip(pts, bars)):
        x0 = (pts[i - 1][0] + x) / 2 if i else LEFT
        x1 = (x + pts[i + 1][0]) / 2 if i < len(pts) - 1 else x + 6
        out.append(f'<g class="pt" tabindex="0" data-tip="{_e(f"{p:g}|{d:%d/%m/%Y}|cierre")}"><rect x="{x0:.1f}" y="{TOP}" width="{x1 - x0:.1f}" '
                   f'height="{H - TOP - BOTTOM}" fill="transparent"/><line class="xh" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{H - BOTTOM}"/>'
                   f'<circle class="dt" cx="{x:.1f}" cy="{y:.1f}" r="4.5"/></g>')

    out.append(f'<line x1="{px(today):.1f}" x2="{px(today):.1f}" y1="{TOP}" y2="{H - BOTTOM}" stroke="var(--line-strong)" stroke-dasharray="2 3"/>'
               f'<text class="ax" x="{px(today) + 3:.1f}" y="{H - BOTTOM - 4}" font-size="10">hoy</text>')

    marks: list[tuple[date, str, str, str]] = []                                       # (fecha, texto, color, tooltip) de las líneas verticales
    for exp in sorted({s.expiry for s in puts}):
        marks.append((exp, f"venc. {exp:%d/%m} · {(exp - today).days} d", EXPIRY, f"{exp:%d/%m/%Y}|Vencimiento|quedan {(exp - today).days} días"))
    for (rd, exp), plan in sorted(reviews.items()):
        if plan.days_to_review is not None and plan.days_to_review > 0:
            marks.append((rd, f"{'revisar' if plan.is_review else f'{cfg.review_dte} DTE'} {rd:%d/%m} · en {plan.days_to_review} d", REVIEW, f"{rd:%d/%m/%Y}|Revisión a {cfg.review_dte} DTE|faltan {plan.days_to_review} días"))
        elif plan.days_to_review is not None and exp >= today:
            marks.append((today, "en zona de revisión" if plan.is_review else f"dentro de {cfg.review_dte} DTE", REVIEW, f"{rd:%d/%m/%Y}|Revisión a {cfg.review_dte} DTE|ya toca (venc. {exp:%d/%m})"))
    for i, (d, text, color, tip) in enumerate(sorted(marks)):
        if d < start:
            continue
        beyond = d > end
        x = px(min(d, end))
        y_text = TOP - 16 + (i % 3) * 11
        anchor = "end" if x > W - 150 else "start"
        dx = -4 if anchor == "end" else 4
        dash = "6 4" if color == EXPIRY else "2 3"
        out.append(f'<g tabindex="0" data-tip="{_e(tip)}"><line x1="{x:.1f}" x2="{x:.1f}" y1="{y_text + 3:.1f}" y2="{H - BOTTOM}" stroke="{color}" stroke-width="1.8" '
                   f'stroke-dasharray="{dash}"/><line x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{H - BOTTOM}" stroke="transparent" stroke-width="10"/>'
                   f'<text x="{x + dx:.1f}" y="{y_text + 8:.1f}" font-size="10.5" font-weight="700" text-anchor="{anchor}" fill="{"var(--ink)" if color == EXPIRY else "#8a5a00"}" '
                   f'stroke="var(--panel)" stroke-width="3" paint-order="stroke">{_e(text + (" →" if beyond else ""))}</text></g>')
    out.append("</svg>")
    return Markup("".join(out))
