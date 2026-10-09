"""Filtros técnicos del scanner (tendencia, medias, zona de soporte, días desde el último toque del strike).

Se calculan al escanear a partir de los cierres diarios guardados (`daily_bars`); nada de esto se guarda.
Los cálculos por ticker se hacen una sola vez por escaneo.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from scanner_opciones.config.settings import TechnicalSettings
from scanner_opciones.metrics import technical as ta
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, ScanCriteria

_LABELS = {"ma50": "MA50", "ma100": "MA100", "ma200": "MA200", "ema9": "EMA9", "ema20": "EMA20"}
_FRAME_NAMES = {"daily": "diarias", "weekly": "semanales", "monthly": "mensuales"}


class TechnicalFilter:
    def __init__(
        self, criteria: ScanCriteria, cfg: TechnicalSettings, bars: dict[str, list[tuple[date, float]]], today: date
    ) -> None:
        self.c, self.cfg, self.bars, self.today = criteria, cfg, bars, today
        self._ticker_cache: dict[str, Optional[str]] = {}
        self._zone_cache: dict[str, Optional[ta.Zone]] = {}
        self._lines_cache: dict[str, tuple[dict[str, Optional[float]], dict[str, Optional[float]], int]] = {}

    def _lines(self, ticker: str) -> tuple[dict[str, Optional[float]], dict[str, Optional[float]], int]:
        """Valor actual de cada media con las velas elegidas (`ma_frame`), su valor de hace `ma_slope_candles`
        velas (para la pendiente) y cuántas velas hay."""
        if ticker not in self._lines_cache:
            closes = [px for _, px in ta.resample(self.bars.get(ticker, []), self.c.ma_frame)]
            back = closes[: -self.cfg.ma_slope_candles]
            lines, before = {}, {}
            for k, (kind, n) in MA_LINES.items():
                fn = ta.sma if kind == "sma" else ta.ema
                lines[k], before[k] = fn(closes, n), fn(back, n)
            self._lines_cache[ticker] = (lines, before, len(closes))
        return self._lines_cache[ticker]

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
            window = ta.last_months(series, self.today, c.trend_window_months)
            if c.trend_method == "swings":
                ok, why = ta.trend_swings(window, up, price, cfg.trend_pivot_width, cfg.trend_swings_required)
            else:
                ok, why = ta.trend_unbroken_extreme(
                    window, up, price, self.today, c.trend_min_days, cfg.trend_min_progress_pct
                )
            if not ok:
                return f"tendencia {'alcista' if up else 'bajista'}: {why}"
        lines, before, n_bars = self._lines(ticker)
        frame = _FRAME_NAMES[c.ma_frame]

        def missing(key: str) -> str:
            return f"sin datos para {_LABELS[key]} ({n_bars} velas {frame}, hacen falta {MA_LINES[key][1]})"

        for key in MA_LINES:
            side = getattr(c, key)
            if side == "any":
                continue
            if lines[key] is None:
                return missing(key)
            if (side == "above") != (price > lines[key]):
                return f"precio {'no está por encima' if side == 'above' else 'no está por debajo'} de {_LABELS[key]} ({frame})"
        for field, (short, long_) in MA_CROSSES.items():
            side = getattr(c, field)
            if side == "any":
                continue
            for key in (short, long_):
                if lines[key] is None:
                    return missing(key)
            a, b = lines[short], lines[long_]
            if (a >= b) if side == "gte" else (a <= b):
                continue
            return f"{_LABELS[short]} {'<' if side == 'gte' else '>'} {_LABELS[long_]} ({frame})"
        for field, key in MA_SLOPES.items():
            side = getattr(c, field)
            if side == "any":
                continue
            if lines[key] is None:
                return missing(key)
            if before[key] is None:
                return (f"sin datos para la pendiente de {_LABELS[key]} ({n_bars} velas {frame}, hacen falta "
                        f"{MA_LINES[key][1] + cfg.ma_slope_candles})")
            if (lines[key] > before[key]) if side == "up" else (lines[key] < before[key]):
                continue
            return f"{_LABELS[key]} no {'sube' if side == 'up' else 'baja'} ({frame}, últimas {cfg.ma_slope_candles} velas)"
        return None
