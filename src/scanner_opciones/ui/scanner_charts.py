"""Vista general de los resultados del Scanner: dispersión descuento/yield, sectores y distribución del yield."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from markupsafe import Markup

from scanner_opciones.ui import viz

MAX_POINTS = 900          # un escaneo puede dar miles de contratos: se dibujan los de mayor yield


def _pct(v: float) -> str:
    return f"{v:.0f} %"


def _buckets(results) -> list[tuple[int, int]]:
    """Cuatro tramos de DTE de igual ancho entre el menor y el mayor de los resultados."""
    dtes = [r.dte for r in results]
    lo, hi = min(dtes), max(dtes)
    step = max(1, math.ceil((hi - lo + 1) / 4))
    return [(lo + i * step, min(hi, lo + (i + 1) * step - 1)) for i in range(4)]


def overview(results: Sequence) -> Optional[dict]:
    """Los tres gráficos (HTML ya seguro) y datos auxiliares, o None si no hay resultados."""
    rows = [r for r in results if r.yield_ref_annualized_pct is not None and r.strike_distance_pct is not None]
    if not rows:
        return None
    ranked = sorted(rows, key=lambda r: r.yield_ref_annualized_pct, reverse=True)
    shown = ranked[:MAX_POINTS]
    buckets = _buckets(shown)

    def bucket(dte: int) -> int:
        return next((i for i, (a, b) in enumerate(buckets) if a <= dte <= b), len(buckets) - 1)

    points = []
    for r in shown:
        c = r.snapshot.contract
        tip = (f"{r.yield_ref_annualized_pct:.1f} %|{c.ticker} {c.strike:g} · {c.expiry.strftime('%d/%m')}"
               f"|DTE {r.dte} · descuento {r.strike_distance_pct:.1f} %")
        points.append((r.strike_distance_pct, r.yield_ref_annualized_pct, tip, bucket(r.dte)))
    scatter = viz.scatter(points, "Descuento del strike (%)", "Yield anual (%)", x_fmt=lambda v: f"{v:.0f}", y_fmt=_pct,
                          height=240, label="Yield anual frente al descuento del strike, un punto por contrato")
    key = viz.legend([(f"DTE {a}–{b}" if a != b else f"DTE {a}", viz.ORDINAL[i]) for i, (a, b) in enumerate(buckets)])

    # sectores: nº de contratos que cumplen (los 8 mayores y «Otros»)
    per_sector: dict[str, int] = {}
    tickers: dict[str, set] = {}
    for r in rows:
        sector = r.impact.sector or "Sin sector"
        per_sector[sector] = per_sector.get(sector, 0) + 1
        tickers.setdefault(sector, set()).add(r.snapshot.contract.ticker)
    top = viz.top_with_other({k: float(v) for k, v in per_sector.items()}, 8)
    named = {name for name, _ in top if name != viz.OTHER}
    sector_rows = []
    for name, n in top:
        pool = tickers[name] if name != viz.OTHER else set().union(*(t for k, t in tickers.items() if k not in named))
        sector_rows.append((name, n, f"{int(n)} contratos de {len(pool)} tickers"))
    sectors = viz.hbars(sector_rows, fmt=lambda v: f"{v:.0f}", message="Sin resultados")

    # distribución del yield anual: 8 columnas desde el mínimo hasta el percentil 95 (la última recoge el resto)
    ys = sorted(r.yield_ref_annualized_pct for r in rows)
    lo, hi = ys[0], ys[min(len(ys) - 1, int(len(ys) * 0.95))]
    width = max(1.0, round((hi - lo) / 8)) if hi > lo else 1.0
    counts = [0] * 8
    for y in ys:
        counts[min(7, int((y - lo) // width))] += 1
    items = []
    for i, n in enumerate(counts):
        a = lo + i * width
        label = f"{a:.0f}+" if i == 7 else f"{a:.0f}"
        span = f"{a:.0f} % o más" if i == 7 else f"{a:.0f}–{a + width:.0f} %"
        items.append((label, float(n), f"{n} contratos|yield anual {span}"))
    columns = viz.columns(items, width=330, height=150, label="Contratos por tramo de yield anual")
    return {"scatter": scatter, "scatter_key": key, "sectors": sectors, "histogram": columns,
            "count": len(rows), "drawn": len(shown), "yield_max": ys[-1]}

