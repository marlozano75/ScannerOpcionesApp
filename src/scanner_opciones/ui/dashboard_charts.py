"""Gráficos del Panel: medidores de riesgo, exposición, VIX (cierres y curva de futuros), sectores y semanas."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from markupsafe import Markup

from scanner_opciones.ui import viz


def _money(v: float) -> str:
    return f"{v:,.0f}"


def _dm(d: date) -> str:
    return d.strftime("%d/%m")


def cushion_meter(metric, thresholds) -> Markup:
    """Medidor del cushion (0-60 %): el relleno lleva el estado del semáforo y las marcas, los umbrales de la configuración."""
    if metric is None or metric.cushion_pct is None:
        return Markup("")
    return viz.meter(
        metric.cushion_pct, 0, 60, viz.STATUS.get(metric.light.value, viz.STATUS["unknown"]),
        ticks=[(thresholds.concern_above, f"{thresholds.concern_above:g}%"), (thresholds.normal_above, f"{thresholds.normal_above:g}%")],
        label=f"Cushion {metric.cushion_pct:.1f} %")


def exposure_bars(assignment) -> Markup:
    """Valor liquidativo, posiciones y exposición nominal a asignación en la misma escala: el apalancamiento se ve."""
    rows = [
        ("Net Liquidation", assignment.net_liquidation or 0.0, "Valor liquidativo: lo que tienes en la cuenta"),
        ("Gross Position Value", assignment.gross_position_value or 0.0, "Valor de mercado de las posiciones"),
        ("Nominal Assignment Exposure", assignment.nominal_assignment_exposure or 0.0,
         "Si te asignan todas las puts: nominal de las vendidas menos las compradas"),
    ]
    return viz.hbars(rows, fmt=_money, message="Sin posiciones")


def vix_history(vix) -> Markup:
    """Cierres recientes del VIX y el valor actual como último punto."""
    points = [(i, v, f"{v:.2f}|{d.strftime('%d/%m/%Y')}") for i, (d, v) in enumerate(vix.last_closes)]
    ticks = [(0, _dm(vix.last_closes[0][0]))] if vix.last_closes else []
    if vix.current is not None and points:
        points.append((len(points), vix.current, f"{vix.current:.2f}|Ahora"))
        ticks.append((len(points) - 1, "ahora"))
    elif points:
        ticks.append((len(points) - 1, _dm(vix.last_closes[-1][0])))
    return viz.line_chart(points, ticks, y_fmt=lambda v: f"{v:.1f}", height=130, label="Cierres recientes del VIX")


def vix_curve(vix, today: date) -> tuple[Markup, Optional[str]]:
    """Curva de futuros del VIX (hoy = VIX actual): el título dice si está en contango o en backwardation."""
    if vix.current is None or not vix.futures:
        return viz.empty("Sin futuros del VIX"), None
    points = [(0, vix.current, f"{vix.current:.2f}|VIX hoy")]
    ticks = [(0, "hoy")]
    for d, price in vix.futures:
        days = max((d - today).days, 1)
        points.append((days, price, f"{price:.2f}|Futuro {d.strftime('%d/%m/%Y')}|a {days} días"))
        ticks.append((days, _dm(d)))
    shape = ("Contango: los futuros cotizan por encima del VIX (lo normal, mercado tranquilo)"
             if vix.futures[0][1] > vix.current else
             "Backwardación: los futuros cotizan por debajo del VIX (el mercado paga por protegerse ya)")
    return viz.line_chart(points, ticks, y_fmt=lambda v: f"{v:.1f}", height=130, markers=True, label="Curva de futuros del VIX"), shape


MAX_NAMES = 6      # tickers que se escriben bajo cada barra; el resto («+3») va en el tooltip


def _names(amounts: dict[str, float]) -> str:
    """«AAPL · MSFT · NVDA +2»: los tickers por exposición descendente."""
    ranked = [t for t, _ in sorted(amounts.items(), key=lambda kv: kv[1], reverse=True)]
    return " · ".join(ranked[:MAX_NAMES]) + (f" +{len(ranked) - MAX_NAMES}" if len(ranked) > MAX_NAMES else "")


def _detail(amounts: dict[str, float]) -> str:
    return ", ".join(f"{t} {_money(a)}" for t, a in sorted(amounts.items(), key=lambda kv: kv[1], reverse=True))


def _merge(by_sector: dict[str, dict[str, float]], sectors) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in sectors:
        for t, a in by_sector.get(s, {}).items():
            out[t] = out.get(t, 0.0) + a
    return out


def sector_bars(sectors) -> Markup:
    top = viz.top_with_other(sectors.weights_pct, 8)
    kept = {name for name, _ in top} - {viz.OTHER}
    by = getattr(sectors, "tickers", {}) or {}
    notes = {}
    for name, _ in top:
        amounts = _merge(by, [s for s in by if s not in kept] if name == viz.OTHER else [name])
        if amounts:
            notes[name] = _names(amounts)
    rows = [(name, w, f"{w:.1f} % de la exposición potencial" + (f" · {_detail(_merge(by, [s for s in by if s not in kept] if name == viz.OTHER else [name]))}" if name in notes else ""))
            for name, w in top]
    return viz.hbars(rows, fmt=lambda v: f"{v:.1f} %", message="Sin posiciones", notes=notes)


WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _expiry_days(days) -> str:
    """«vie 30/10»: los días de la semana en que caducan las opciones."""
    return ", ".join(f"{WEEKDAYS[d.weekday()]} {_dm(d)}" for d in days)


def week_bars(weeks, today: Optional[date] = None) -> Markup:
    """Una barra por semana de vencimiento con el nominal de puts vendidas apilado por sector (los 7 mayores y «Otros»)."""
    totals: dict[str, float] = {}
    for w in weeks:
        for sector, amount in w.amounts.items():
            totals[sector] = totals.get(sector, 0.0) + amount
    ranked = viz.top_with_other(totals, viz.SERIES)
    order = [name for name, _ in ranked]
    keep = set(order) - {viz.OTHER}
    rows, notes, seg_notes, now = [], {}, {}, set()
    for w in weeks:
        merged: dict[str, float] = {}
        for sector, amount in w.amounts.items():
            key = sector if sector in keep else viz.OTHER
            merged[key] = merged.get(key, 0.0) + amount
        current = today is not None and w.week_start <= today < w.week_start + timedelta(days=7)
        label = (_expiry_days(getattr(w, "expiries", ())) or f"Sem. {_dm(w.week_start)}") + (" · esta semana" if current else "")
        if current:
            now.add(label)
        rows.append((label, [(name, merged[name]) for name in order if name in merged], _money(w.total)))
        by = getattr(w, "tickers", {}) or {}
        if by:
            notes[label] = _names(_merge(by, list(by)))
            for name in merged:
                seg_notes[(label, name)] = _detail(_merge(by, [s for s in by if s not in keep] if name == viz.OTHER else [name]))
    return viz.stacked_hbars(rows, order, fmt=_money, message="Sin puts vendidas en estas semanas", notes=notes, seg_notes=seg_notes, highlight=now)
