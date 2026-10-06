"""Criterios de escaneo construidos desde la configuración."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import PriceReference


MA_LINES = {"ma50": ("sma", 50), "ma100": ("sma", 100), "ma200": ("sma", 200), "ema9": ("ema", 9), "ema20": ("ema", 20)}


@dataclass(frozen=True)
class ScanCriteria:
    strike_below_pct_min: float
    strike_below_pct_max: float
    min_annual_yield_pct: float
    dte_min: int
    dte_max: int
    min_oi: Optional[int] = None
    min_bid_size: Optional[int] = None
    max_spread_pct: Optional[float] = None
    min_iv_rank: Optional[float] = None
    min_iv_percentile: Optional[float] = None
    only_uptrend: bool = False
    # filtros técnicos (ver scanner/technical.py); los valores por defecto no filtran nada
    trend_direction: str = "off"          # off | up | down
    trend_method: str = "low"             # low (mínimo/máximo sin romper) | swings (máximos y mínimos crecientes)
    trend_frame: str = "daily"            # daily | weekly | monthly
    trend_min_days: int = 30              # antigüedad mínima del mínimo sin romper (método «low»)
    ma50: str = "any"                     # any | above | below (precio respecto a cada línea)
    ma100: str = "any"
    ma200: str = "any"
    ema9: str = "any"
    ema20: str = "any"
    require_support: bool = False
    min_days_since_touch: Optional[int] = None
    min_price: Optional[float] = None     # precio del subyacente (vacío = sin límite)
    max_price: Optional[float] = None

    @property
    def technical_active(self) -> bool:
        return (
            self.trend_direction != "off" or self.require_support or self.min_days_since_touch is not None
            or any(getattr(self, k) != "any" for k in MA_LINES)
        )
    price_reference: PriceReference = PriceReference.BID_PLUS_SPREAD
    price_spread_pct: float = 25.0

    def with_filters(self, **overrides) -> "ScanCriteria":
        """Copia con filtros modificados (p. ej. desde la UI)."""
        return replace(self, **overrides)


def criteria_from_settings(settings: Settings) -> ScanCriteria:
    """Valores iniciales del filtro (todos editables en el formulario)."""
    f = settings.scanner.filters
    ini = settings.scanner.initial
    return ScanCriteria(
        strike_below_pct_min=ini.strike_below_pct_min,
        strike_below_pct_max=ini.strike_below_pct_max,
        min_annual_yield_pct=ini.min_annual_yield_pct,
        dte_min=ini.dte_min,
        dte_max=ini.dte_max,
        min_oi=f.min_oi, min_bid_size=f.min_bid_size, max_spread_pct=f.max_spread_pct,
        min_iv_rank=f.min_iv_rank, min_iv_percentile=f.min_iv_percentile,
        only_uptrend=f.only_uptrend,
        price_reference=settings.scanner.price_reference.mode,
        price_spread_pct=settings.scanner.price_reference.spread_pct,
    )
