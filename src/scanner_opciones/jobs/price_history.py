"""Histórico de cierres diarios guardado en la base de datos: solo se piden los días que faltan.

Primera vez (ticker sin histórico): `trend.history_days` días. Después: desde el último día guardado
(con unos días de solape para detectar ajustes por splits o dividendos). Si los cierres solapados no
coinciden con los guardados, el histórico de ese ticker se descarta y se vuelve a pedir entero.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from scanner_opciones.config.settings import TrendSettings
from scanner_opciones.domain.errors import CandleError
from scanner_opciones.marketdata.candles import CandleProvider, DailyBars
from scanner_opciones.storage.repositories import BarRepo

log = logging.getLogger(__name__)

OVERLAP_DAYS = 7            # días ya guardados que se vuelven a pedir para comprobar que no hay ajustes
ADJUSTMENT_TOLERANCE = 0.001   # diferencia relativa a partir de la cual un cierre solapado indica un ajuste


def _closed(bars: DailyBars, today: date) -> DailyBars:
    return [(d, px) for d, px in bars if d < today]   # la barra de hoy está en curso


def _adjusted(stored: dict[date, float], fresh: DailyBars) -> bool:
    return any(
        d in stored and abs(px / stored[d] - 1) > ADJUSTMENT_TOLERANCE for d, px in fresh
    )


async def update_history(
    candles: CandleProvider, repo: BarRepo, tickers: list[str], today: date, cfg: TrendSettings
) -> None:
    """Completa el histórico de cierres de `tickers`. Si el proveedor falla se conserva lo ya guardado."""
    last = repo.last_days(tickers)
    # al día (el último cierre guardado es de ayer o posterior): no hay nada que pedir
    stale = [t for t in tickers if t in last and (today - last[t]).days > 1]
    full = [t for t in tickers if t not in last]
    stats_note = {"incremental": 0, "full": 0}
    try:
        if stale:
            days = max((today - last[t]).days for t in stale) + OVERLAP_DAYS
            fetched = await candles.get_daily_closes(stale, days)
            for t in stale:
                series = _closed(fetched.get(t, []), today)
                if series and _adjusted(repo.closes(t, official_only=True), series):
                    log.info("%s: los cierres guardados no coinciden con los actuales (¿split?); se recarga el histórico", t)
                    repo.delete(t)
                    full.append(t)
                else:
                    repo.upsert(t, series)
                    stats_note["incremental"] += 1
        if full:
            fetched = await candles.get_daily_closes(full, cfg.history_days)
            for t in full:
                repo.upsert(t, _closed(fetched.get(t, []), today))
                stats_note["full"] += 1
    except CandleError as exc:
        log.warning("Velas no disponibles, se conserva el histórico guardado: %s", exc)
    repo.prune(today - timedelta(days=cfg.history_days))
    log.info("Histórico de cierres: %d completos, %d incrementales, %d al día", stats_note["full"],
             stats_note["incremental"], len(tickers) - stats_note["full"] - stats_note["incremental"])
