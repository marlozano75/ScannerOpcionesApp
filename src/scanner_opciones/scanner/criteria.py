"""Criterios de escaneo construidos desde la configuración."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import PriceReference


MA_LINES = {"ma50": ("sma", 50), "ma100": ("sma", 100), "ma200": ("sma", 200), "ema9": ("ema", 9), "ema20": ("ema", 20)}
# comparaciones entre medias: campo del criterio -> (línea corta, línea larga); valores any | gte (≥) | lte (≤)
MA_CROSSES = {
    "cmp_ema9_ema20": ("ema9", "ema20"), "cmp_ema20_ma50": ("ema20", "ma50"),
    "cmp_ma50_ma100": ("ma50", "ma100"), "cmp_ma100_ma200": ("ma100", "ma200"),
}
# pendiente de cada media: campo del criterio -> línea; valores any | up (sube) | down (baja)
MA_SLOPES = {f"slope_{k}": k for k in MA_LINES}


def frame_capacity(frame: str, history_days: int) -> int:
    """Velas que caben en el histórico guardado (`trend.history_days`): con 1400 días tastytrade da 944 diarias,
    198 semanales y 46 mensuales (las diarias son solo sesiones bursátiles)."""
    return {"daily": history_days * 944 // 1400, "weekly": history_days // 7 - 2, "monthly": history_days // 30}[frame]


def unavailable_ma_fields(frame: str, history_days: int, slope_candles: int) -> set[str]:
    """Campos de medias que no se pueden calcular con las velas `frame` y ese histórico: la media necesita tantas
    velas como su periodo, una comparación las de ambas líneas y la pendiente además `slope_candles` más."""
    cap = frame_capacity(frame, history_days)
    lines = {k for k, (_, n) in MA_LINES.items() if n > cap}
    slopes = {f: k for f, k in MA_SLOPES.items() if MA_LINES[k][1] + slope_candles > cap}
    crosses = {f for f, (a, b) in MA_CROSSES.items() if a in lines or b in lines}
    return lines | crosses | set(slopes)


# Filtros de solvencia / calidad del flujo de caja: (campo del criterio, atributo de TickerInfo, "max"|"min", etiqueta)
SOLVENCY_RULES = (
    ("max_debt_to_equity", "debt_to_equity", "max", "deuda/patrimonio"),
    ("min_interest_coverage", "interest_coverage", "min", "cobertura de intereses"),
    ("min_cash_to_short_debt", "cash_to_short_debt", "min", "efectivo / deuda a corto plazo"),
    ("min_ocf_to_debt", "ocf_to_debt", "min", "flujo operativo / deuda"),
    ("max_capex_to_ocf", "capex_to_ocf", "max", "capex / flujo operativo"),
    ("min_fcf_to_assets", "fcf_to_assets", "min", "FCF / activos"),
    ("min_net_buyback_pct", "net_buyback_pct", "min", "recompra neta de acciones"),
    ("min_roic", "roic", "min", "ROIC"),
    ("max_loss_years", "loss_years", "max", "años con pérdidas (últimos 10)"),
    ("max_revenue_drops", "revenue_drop_years", "max", "años con caída de ingresos (últimos 10)"),
    ("max_earnings_volatility", "earnings_volatility", "max", "volatilidad de los beneficios"),
)


@dataclass(frozen=True)
class ScanCriteria:
    strike_below_pct_min: float
    strike_below_pct_max: float
    min_annual_yield_pct: float
    dte_min: int
    dte_max: int
    min_oi: Optional[int] = None
    min_bid: Optional[float] = None
    min_bid_size: Optional[int] = None
    max_spread_pct: Optional[float] = None
    min_iv_rank: Optional[float] = None
    min_iv_percentile: Optional[float] = None
    # filtros técnicos (ver scanner/technical.py); los valores por defecto no filtran nada
    trend_direction: str = "off"          # off | up | down
    trend_method: str = "low"             # low (mínimo/máximo sin romper) | swings (máximos y mínimos crecientes) | lows (solo mínimos crecientes / máximos decrecientes)
    trend_window_months: int = 24         # la tendencia se evalúa con los cierres de los últimos N meses
    trend_min_days: int = 30              # antigüedad mínima del mínimo sin romper (método «low»)
    ma50: str = "any"                     # any | above | below (precio respecto a cada línea)
    ma100: str = "any"
    ma200: str = "any"
    ema9: str = "any"
    ema20: str = "any"
    ma_frame: str = "daily"               # velas de las medias: daily | weekly | monthly (50 = 50 días, 50 semanas o 50 meses)
    cmp_ema9_ema20: str = "any"           # any | gte | lte
    cmp_ema20_ma50: str = "any"
    cmp_ma50_ma100: str = "any"
    cmp_ma100_ma200: str = "any"
    slope_ma50: str = "any"               # any | up | down (valor actual de la media frente al de hace `ma_slope_candles` velas)
    slope_ma100: str = "any"
    slope_ma200: str = "any"
    slope_ema9: str = "any"
    slope_ema20: str = "any"
    require_support: bool = False
    min_days_since_touch: Optional[int] = None
    min_price: Optional[float] = None     # precio del subyacente (vacío = sin límite)
    max_price: Optional[float] = None
    # calidad de la empresa (ver scanner/quality.py); los valores por defecto no filtran nada
    require_profitable: bool = False              # EPS de los últimos 12 meses > 0 (sin dato = se descarta)
    min_positive_quarters: Optional[int] = None   # trimestres con beneficios exigidos de los últimos 4
    min_option_liquidity: Optional[int] = None    # liquidez de las opciones (1-5) mínima
    avoid_earnings: bool = False                  # descarta los vencimientos posteriores a la próxima fecha de resultados
    max_liabilities_to_equity: Optional[float] = None   # pasivo/patrimonio máximo (las financieras no se miden)
    require_positive_fcf: bool = False            # flujo de caja libre de 12 meses > 0 (las financieras no se miden)
    # solvencia y calidad del flujo de caja (grados de exigencia en scanner.quality.thresholds); los sectores exentos no se miden
    max_debt_to_equity: Optional[float] = None    # deuda financiera / patrimonio
    min_interest_coverage: Optional[float] = None  # resultado operativo / intereses (veces)
    min_cash_to_short_debt: Optional[float] = None  # efectivo / deuda a corto plazo (veces)
    min_ocf_to_debt: Optional[float] = None       # flujo de caja operativo / deuda total (fracción)
    max_capex_to_ocf: Optional[float] = None      # inversión en inmovilizado / flujo operativo (fracción)
    min_fcf_to_assets: Optional[float] = None     # flujo de caja libre / activos (fracción)
    min_net_buyback_pct: Optional[float] = None   # reducción del nº de acciones en el último año (%)
    min_roic: Optional[float] = None              # ROIC: resultado operativo tras impuestos / (deuda + patrimonio) (fracción)
    max_loss_years: Optional[int] = None          # años con pérdidas máximos de los últimos 10 años fiscales
    max_revenue_drops: Optional[int] = None       # años con caída de ingresos máximos de los últimos 10 años fiscales
    max_earnings_volatility: Optional[float] = None  # desviación típica máxima del crecimiento anual del beneficio (fracción)
    # «deuda baja o manejable»: pasa con deuda/patrimonio ≤ manageable_max_de O con cobertura de intereses ≥ manageable_min_cover
    require_manageable_debt: bool = False
    manageable_max_de: float = 0.5
    manageable_min_cover: float = 3.0

    @property
    def ticker_quality_active(self) -> bool:
        """¿Hay algún filtro de calidad de la empresa (no cuenta el de resultados, que es por contrato)?"""
        return (
            self.require_profitable or self.min_positive_quarters is not None
            or self.min_option_liquidity is not None
            or self.max_liabilities_to_equity is not None or self.require_positive_fcf
            or self.require_manageable_debt
            or any(getattr(self, field) is not None for field, *_ in SOLVENCY_RULES)
        )

    @property
    def quality_active(self) -> bool:
        return (
            self.require_profitable or self.avoid_earnings or self.min_positive_quarters is not None
            or self.min_option_liquidity is not None
            or self.max_liabilities_to_equity is not None or self.require_positive_fcf
            or self.require_manageable_debt
            or any(getattr(self, field) is not None for field, *_ in SOLVENCY_RULES)
        )

    @property
    def technical_active(self) -> bool:
        return (
            self.trend_direction != "off" or self.require_support or self.min_days_since_touch is not None
            or any(getattr(self, k) != "any" for k in (*MA_LINES, *MA_CROSSES, *MA_SLOPES))
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
        min_oi=f.min_oi, min_bid=f.min_bid, min_bid_size=f.min_bid_size, max_spread_pct=f.max_spread_pct,
        min_iv_rank=f.min_iv_rank, min_iv_percentile=f.min_iv_percentile,
        manageable_max_de=settings.scanner.quality.manageable_debt.max_debt_to_equity,
        manageable_min_cover=settings.scanner.quality.manageable_debt.min_interest_coverage,
        price_reference=settings.scanner.price_reference.mode,
        price_spread_pct=settings.scanner.price_reference.spread_pct,
    )
