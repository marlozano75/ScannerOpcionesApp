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
from scanner_opciones.marketdata.candles import CandleProvider, OhlcBars
from scanner_opciones.storage.repositories import BarRepo

log = logging.getLogger(__name__)

OVERLAP_DAYS = 7            # días ya guardados que se vuelven a pedir para comprobar que no hay ajustes
ADJUSTMENT_TOLERANCE = 0.001   # diferencia relativa a partir de la cual un cierre solapado indica un ajuste


def _last_expected_close(today: date) -> date:
    """Último día laborable anterior a `today` (fines de semana fuera; los festivos no se conocen aquí)."""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


RANGE_DAYS = 20             # sesiones recientes que deben tener máximo y mínimo (el ATR(14) usa ~60 con su suavizado)
RANGE_NEEDED = 15           # de ellas, cuántas deben tener máximo y mínimo para no volver a pedirlas
RANGE_FETCH_DAYS = 45       # días naturales que se vuelven a pedir si faltan máximos y mínimos de las últimas sesiones


def _closed(bars: OhlcBars, today: date) -> OhlcBars:
    return [b for b in bars if b[0] < today]   # la barra de hoy está en curso


def _adjusted(stored: dict[date, float], fresh: OhlcBars) -> bool:
    return any(
        d in stored and abs(px / stored[d] - 1) > ADJUSTMENT_TOLERANCE for d, px, *_ in fresh
    )


async def _fetch(candles: CandleProvider, tickers: list[str], days: int) -> dict[str, OhlcBars]:
    """Velas con máximo y mínimo si el proveedor las da; si no, solo cierres."""
    if hasattr(candles, "get_daily_ohlc"):
        return await candles.get_daily_ohlc(tickers, days)
    return {t: [(d, px, None, None) for d, px in bars] for t, bars in (await candles.get_daily_closes(tickers, days)).items()}


async def update_history(
    candles: CandleProvider, repo: BarRepo, tickers: list[str], today: date, cfg: TrendSettings
) -> None:
    """Completa el histórico de cierres de `tickers`. Si el proveedor falla se conserva lo ya guardado."""
    last = repo.last_days(tickers)
    # al día (ya está el último cierre laborable, p. ej. el del viernes en fin de semana): no hay nada que pedir
    expected = _last_expected_close(today)
    # al día pero sin máximos ni mínimos en las últimas sesiones (histórico anterior a la migración v20): se vuelven a pedir
    no_ranges = set(repo.missing_ranges([t for t in tickers if t in last], RANGE_DAYS, RANGE_NEEDED))
    stale = [t for t in tickers if t in last and (last[t] < expected or t in no_ranges)]
    full = [t for t in tickers if t not in last]
    stats_note = {"incremental": 0, "full": 0}
    try:
        if stale:
            days = max((today - last[t]).days + OVERLAP_DAYS if t not in no_ranges else max((today - last[t]).days + OVERLAP_DAYS, RANGE_FETCH_DAYS)
                       for t in stale)
            fetched = await _fetch(candles, stale, days)
            for t in stale:
                series = _closed(fetched.get(t, []), today)
                if series and _adjusted(repo.closes(t, official_only=True), series):
                    log.info("%s: los cierres guardados no coinciden con los actuales (¿split?); se recarga el histórico", t)
                    repo.delete(t)
                    full.append(t)
                else:
                    repo.upsert_ohlc(t, series)
                    stats_note["incremental"] += 1
        if full:
            fetched = await _fetch(candles, full, cfg.history_days)
            for t in full:
                repo.upsert_ohlc(t, _closed(fetched.get(t, []), today))
                stats_note["full"] += 1
    except CandleError as exc:
        log.warning("Velas no disponibles, se conserva el histórico guardado: %s", exc)
    repo.prune(today - timedelta(days=cfg.history_days))
    log.info("Histórico de cierres: %d completos, %d incrementales, %d al día", stats_note["full"],
             stats_note["incremental"], len(tickers) - stats_note["full"] - stats_note["incremental"])
