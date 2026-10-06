"""Filtros técnicos del scanner (tendencia, medias, zona de soporte, días desde el último toque del strike).

Se calculan al escanear a partir de los cierres diarios guardados (`daily_bars`); nada de esto se guarda.
Los cálculos por ticker se hacen una sola vez por escaneo.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from scanner_opciones.config.settings import TechnicalSettings
from scanner_opciones.metrics import technical as ta
from scanner_opciones.scanner.criteria import MA_LINES, ScanCriteria

_LABELS = {"ma50": "MA50", "ma100": "MA100", "ma200": "MA200", "ema9": "EMA9", "ema20": "EMA20"}


class TechnicalFilter:
    def __init__(
        self, criteria: ScanCriteria, cfg: TechnicalSettings, bars: dict[str, list[tuple[date, float]]], today: date
    ) -> None:
        self.c, self.cfg, self.bars, self.today = criteria, cfg, bars, today
        self._ticker_cache: dict[str, Optional[str]] = {}
        self._zone_cache: dict[str, Optional[ta.Zone]] = {}

    def reject(self, ticker: str, price: float, strike: float) -> Optional[str]:
        """Motivo de descarte del contrato por los filtros técnicos, o None si pasa."""
        if ticker not in self._ticker_cache:
            self._ticker_cache[ticker] = self._ticker_reason(ticker, price)
        if (why := self._ticker_cache[ticker]) is not None:
            return why
        series = self.bars.get(ticker, [])
        if self.c.require_support:
            zone = self._zone(ticker, price)
            if zone is None:
                return "sin zona de soporte probada bajo el precio"
            if strike > zone.high + 1e-9:
                return f"strike {strike:g} por encima del soporte ({zone.high:g})"
        if (minimum := self.c.min_days_since_touch) is not None:
            days = ta.days_since_touch(series, strike, self.today)
            if days is not None and days < minimum:
                return f"el cierre visitó el strike hace {days} días (< {minimum})"
        return None

    def _zone(self, ticker: str, price: float) -> Optional[ta.Zone]:
        if ticker not in self._zone_cache:
            cfg = self.cfg
            zones = ta.support_zones(
                self.bars.get(ticker, []), self.today, cfg.support_lookback_days, cfg.support_band_pct,
                cfg.support_min_touches, cfg.support_min_clusters, cfg.support_pivot_width, cfg.support_cluster_gap_days,
            )
            self._zone_cache[ticker] = ta.best_support(zones, price)
        return self._zone_cache[ticker]

    def _ticker_reason(self, ticker: str, price: float) -> Optional[str]:
        c, cfg = self.c, self.cfg
        series = self.bars.get(ticker, [])
        if not series:
            return "sin histórico de cierres"
        if c.trend_direction != "off":
            up = c.trend_direction == "up"
            if c.trend_method == "swings":
                f = cfg.frame(c.trend_frame)
                ok, why = ta.trend_swings(series, up, price, c.trend_frame, f.pivot_width, f.lookback_bars, f.swings_required)
            else:
                ok, why = ta.trend_unbroken_extreme(
                    ta.resample(series, c.trend_frame), up, price, self.today, c.trend_min_days, cfg.trend_min_progress_pct
                )
            if not ok:
                return f"tendencia {'alcista' if up else 'bajista'}: {why}"
        closes = [px for _, px in series]
        for key, (kind, n) in MA_LINES.items():
            side = getattr(c, key)
            if side == "any":
                continue
            line = (ta.sma if kind == "sma" else ta.ema)(closes, n)
            if line is None:
                return f"sin datos para {_LABELS[key]}"
            if (side == "above") != (price > line):
                return f"precio {'no está por encima' if side == 'above' else 'no está por debajo'} de {_LABELS[key]}"
        return None
