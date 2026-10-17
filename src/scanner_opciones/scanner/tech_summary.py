"""Resumen técnico de cada contrato del Scanner: cuántas de las señales técnicas habituales se cumplen hoy.

Es solo informativo (no filtra) y no depende de los filtros marcados: se miden siempre las mismas seis comprobaciones, con los umbrales
de la estrategia (`scanner.strategy`) y los parámetros de `scanner.technical`. Una comprobación sin datos suficientes queda «sin dato» y
no cuenta en el total. Funciones puras sobre los cierres diarios guardados.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Sequence

from scanner_opciones.config.settings import StrategySettings, TechnicalSettings
from scanner_opciones.metrics import technical as ta


@dataclass(frozen=True)
class TechCheck:
    short: str
    label: str
    ok: Optional[bool]      # None = sin dato
    text: str


@dataclass(frozen=True)
class TechSummary:
    checks: tuple[TechCheck, ...]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def total(self) -> int:
        return sum(1 for c in self.checks if c.ok is not None)

    @property
    def detail(self) -> str:
        return "\n".join(f"{'✓' if c.ok else '✗' if c.ok is False else '·'} {c.label}: {c.text}" for c in self.checks)


class TechSummarizer:
    """Calcula el resumen de cada ticker una sola vez (el RSI y las zonas recorren todo el histórico) y el del strike aparte."""

    def __init__(self, bars: Mapping[str, Sequence[tuple[date, float]]], cfg: TechnicalSettings, strategy: StrategySettings, today: date) -> None:
        self.bars, self.cfg, self.strategy, self.today = bars, cfg, strategy, today
        self._ticker: dict[tuple[str, float], tuple[list[TechCheck], Optional[ta.Zone]]] = {}

    def _ticker_checks(self, ticker: str, price: float) -> tuple[list[TechCheck], Optional[ta.Zone]]:
        key = (ticker, price)
        if key in self._ticker:
            return self._ticker[key]
        s, cfg = self.strategy, self.cfg
        series = list(self.bars.get(ticker, ()))
        closes = [px for _, px in series]
        none = lambda short, label: TechCheck(short, label, None, "sin histórico suficiente")   # noqa: E731
        checks: list[TechCheck] = []

        ma50, ma200 = ta.sma(closes, 50), ta.sma(closes, 200)
        checks.append(TechCheck("200", "Precio sobre la MA200", price > ma200, f"precio {price:g} frente a MA200 {ma200:.2f}")
                      if ma200 else none("200", "Precio sobre la MA200"))
        checks.append(TechCheck("T", "Tendencia alcista (MA50 > MA200)", ma50 > ma200, f"MA50 {ma50:.2f} frente a MA200 {ma200:.2f}")
                      if ma50 and ma200 else none("T", "Tendencia alcista (MA50 > MA200)"))

        label = f"RSI({cfg.rsi_period}) rebota desde ≤ {s.max_rsi_level:g}"
        if len(closes) > cfg.rsi_period + cfg.rsi_lookback_days:
            ok, why = ta.rsi_rebound(closes, s.max_rsi_level, cfg.rsi_lookback_days, cfg.rsi_period)
            checks.append(TechCheck("RSI", label, ok, "ha tocado el nivel y hoy sube" if ok else why))
        else:
            checks.append(none("RSI", label))

        label = f"Bollinger %B ≤ {s.max_bollinger_pctb:g}"
        bands = ta.bollinger(closes, cfg.bollinger_period, cfg.bollinger_k)
        if bands is not None and bands[2] > bands[0]:
            pctb = (price - bands[0]) / (bands[2] - bands[0])
            checks.append(TechCheck("BB", label, pctb <= s.max_bollinger_pctb + 1e-9, f"%B actual {pctb:.2f}"))
        else:
            checks.append(none("BB", label))

        zone: Optional[ta.Zone] = None
        if series:
            zone = ta.best_support(ta.support_zones(series, self.today, cfg.support_lookback_days, cfg.support_band_pct, cfg.support_min_touches,
                                                    cfg.support_min_clusters, cfg.support_pivot_width, cfg.support_cluster_gap_days), price)
        label = f"Precio a ≤ {s.near_level_pct:g} % de la MA50 o del soporte"
        levels = [v for v in (ma50, zone.high if zone else None) if v]
        if levels:
            nearest = min(abs(price - v) / v * 100 for v in levels)
            checks.append(TechCheck("Niv", label, nearest <= s.near_level_pct + 1e-9, f"a {nearest:.1f} % del nivel más cercano"))
        else:
            checks.append(none("Niv", label))
        self._ticker[key] = (checks, zone)
        return self._ticker[key]

    def summary(self, ticker: str, price: Optional[float], strike: float) -> Optional[TechSummary]:
        """None sin precio. El strike se compara con el soporte probado más alto por debajo del precio."""
        if not price or price <= 0:
            return None
        checks, zone = self._ticker_checks(ticker, price)
        if zone is None:
            sup = TechCheck("Sop", "Strike dentro o bajo el soporte probado", None, "sin zona de soporte probada bajo el precio" if self.bars.get(ticker) else "sin histórico suficiente")
        else:
            sup = TechCheck("Sop", "Strike dentro o bajo el soporte probado", strike <= zone.high + 1e-9,
                            f"strike {strike:g} frente a soporte {zone.low:g}–{zone.high:g}")
        return TechSummary(tuple(checks) + (sup,))
