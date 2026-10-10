"""Gráficos de resumen de la Watchlist."""
from __future__ import annotations

from statistics import median

from scanner_opciones.ui import viz

NO_SECTOR = "Sin sector"


def _sector_rows(counts: dict[str, int], noun: str = "tickers") -> list[tuple[str, float, str]]:
    top = viz.top_with_other({k: float(v) for k, v in counts.items()}, 8)
    return [(name, n, f"{int(n)} {noun}") for name, n in top]


def watchlist_overview(tickers: list[str], infos: dict) -> dict:
    """Distribución del IV Rank (¿dónde está la volatilidad?), sectores y cifras clave de la watchlist."""
    ranks = [infos[t].iv_rank for t in tickers if t in infos and infos[t].iv_rank is not None]
    bins = [0] * 10
    for r in ranks:
        bins[min(9, max(0, int(r // 10)))] += 1
    items = [(f"{i * 10}", float(n), f"{n} tickers|IV Rank {i * 10}–{i * 10 + 10} %") for i, n in enumerate(bins)]
    sectors: dict[str, int] = {}
    for t in tickers:
        sector = (infos[t].sector if t in infos and infos[t].sector else None) or NO_SECTOR
        sectors[sector] = sectors.get(sector, 0) + 1
    soon = sum(1 for t in tickers if t in infos and infos[t].days_to_ex_dividend is not None and infos[t].days_to_ex_dividend <= 10)
    return {
        "iv_hist": viz.columns(items, width=560, height=170, label="Tickers por tramo de IV Rank", message="Sin IV Rank todavía"),
        "sectors": viz.hbars(_sector_rows(sectors), fmt=lambda v: f"{v:.0f}", message="Sin tickers"),
        "median_iv": median(ranks) if ranks else None, "high_iv": sum(1 for r in ranks if r >= 50), "ranked": len(ranks),
        "ex_div_soon": soon,
    }

