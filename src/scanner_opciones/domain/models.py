"""Modelos de dominio (sin dependencias de IBKR ni de la UI)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from scanner_opciones.domain.enums import OptionRight, TrafficLight


@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    sector: Optional[str] = None
    category: Optional[str] = None
    underlying_price: Optional[float] = None
    days_to_ex_dividend: Optional[int] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    updated_daily_at: Optional[datetime] = None
    price_at: Optional[datetime] = None   # cuándo se obtuvo `underlying_price`
    # calidad de la empresa (filtros «Calidad» del scanner); None = sin dato
    eps_ttm: Optional[float] = None            # beneficio por acción de los últimos 12 meses
    positive_quarters: Optional[int] = None    # de los últimos `reported_quarters` trimestres, cuántos con EPS > 0
    reported_quarters: Optional[int] = None
    market_cap: Optional[float] = None         # USD
    option_liquidity: Optional[int] = None     # 1-5, la valoración de tastytrade de la liquidez de sus opciones
    next_earnings: Optional[date] = None       # fecha prevista de los próximos resultados (si ya pasó, es la última)
    eps_surprise_pct: Optional[float] = None   # último EPS frente al consenso, en %
    fundamentals_at: Optional[datetime] = None  # cuándo se descargó el historial trimestral
    liabilities_to_equity: Optional[float] = None   # pasivo total / patrimonio (SEC EDGAR)
    fcf_ttm: Optional[float] = None            # flujo de caja libre de los últimos 12 meses, USD (SEC EDGAR)
    financials_end: Optional[date] = None      # fin del último periodo contable usado
    financials_at: Optional[datetime] = None   # cuándo se consultó EDGAR (aunque no hubiera datos)
    # solvencia y calidad del flujo de caja (SEC EDGAR); 999 = «sin deuda / sin intereses»: no hay nada que cubrir
    debt_to_equity: Optional[float] = None      # deuda financiera / patrimonio
    interest_coverage: Optional[float] = None   # resultado operativo / gastos por intereses (12 meses)
    cash_to_short_debt: Optional[float] = None  # efectivo / deuda a corto plazo
    ocf_to_debt: Optional[float] = None         # flujo de caja operativo / deuda total
    capex_to_ocf: Optional[float] = None        # inversión en inmovilizado / flujo de caja operativo
    fcf_to_assets: Optional[float] = None       # flujo de caja libre / activos totales
    net_buyback_pct: Optional[float] = None     # reducción del nº de acciones diluidas en el último año fiscal, en %
    roic: Optional[float] = None                # ROIC: resultado operativo tras impuestos / (deuda financiera + patrimonio)
    loss_years: Optional[int] = None            # años fiscales con pérdidas de los últimos 10 (None con menos de 5 de historia)
    fiscal_years: Optional[int] = None          # años fiscales evaluados (hasta 10)
    revenue_drop_years: Optional[int] = None    # años en que bajaron los ingresos de los últimos 10 (None con < 5 años de historia)
    revenue_years: Optional[int] = None         # años comparados con el anterior (hasta 9)
    earnings_volatility: Optional[float] = None  # desviación típica del crecimiento anual del beneficio neto (fracción)
    hv30: Optional[float] = None                # volatilidad histórica a 30 días, en % (tastytrade)
    iv30: Optional[float] = None                # volatilidad implícita a 30 días, en % (tastytrade)
    iv_change_5d: Optional[float] = None        # cambio de la IV del subyacente en 5 días, en puntos de IV (+1 = ha subido 1 punto)
    expiry_ivs: Optional[dict] = None           # IV de cada vencimiento (fecha ISO -> fracción), de tastytrade: movimiento esperado


@dataclass(frozen=True)
class OptionContract:
    ticker: str
    expiry: date
    strike: float
    right: OptionRight = OptionRight.PUT
    multiplier: int = 100
    con_id: Optional[int] = None

    def dte(self, today: date) -> int:
        return (self.expiry - today).days


@dataclass(frozen=True)
class ContractSnapshot:
    contract: OptionContract
    updated_at: Optional[datetime]   # None: contrato guardado que aún no se ha cotizado
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    delta: Optional[float] = None
    iv: Optional[float] = None
    open_interest: Optional[int] = None
    spread_pct: Optional[float] = None
    yield_pct: Optional[float] = None
    yield_annualized_pct: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    initial_margin: Optional[float] = None
    bid_size: Optional[int] = None
    margin_at: Optional[datetime] = None  # cuándo se pidió el margen (para reutilizarlo)
    bid_size_tws_at: Optional[datetime] = None   # cuándo se sustituyó `bid_size` por el de TWS (None: es el del proveedor de datos)


@dataclass(frozen=True)
class Position:
    """Acción (`option is None`) o opción abierta."""
    ticker: str
    quantity: float
    market_value: float
    sector: Optional[str] = None
    option: Optional[OptionContract] = None
    avg_cost: Optional[float] = None   # coste medio por contrato en $ (precio de entrada × multiplicador); en una venta, la prima cobrada. En una acción, por acción
    hedge_strike: Optional[float] = None   # put vendida que forma parte de un spread: strike de la put comprada que la cubre (`portfolio/spreads.py`)
    fx: float = 1.0                        # dólares por unidad de la moneda del contrato (1 = USD; ESTX50 en euros): los nominales y riesgos que se suman o comparan con el NLV lo aplican


@dataclass(frozen=True)
class RiskMetric:
    """Un valor de cushion con su semáforo (fracción en %, p. ej. 42.5)."""
    cushion_pct: Optional[float]
    excess_liquidity: Optional[float]
    light: TrafficLight


@dataclass(frozen=True)
class SeverityStatus:
    """HighestSeverity de IBKR: 0 Normal, 1 Advertencia, 2 Riesgo elevado, 3 Liquidación."""
    level: Optional[int]
    light: TrafficLight
    label: str


@dataclass(frozen=True)
class RiskStatus:
    current: RiskMetric
    look_ahead: RiskMetric
    post_expiration: RiskMetric
    severity: "SeverityStatus"
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class SectorExposure:
    """Exposición por sector como % de la exposición potencial total (EPT)."""
    weights_pct: dict[str, float] = field(default_factory=dict)
    total_exposure: float = 0.0
    tickers: dict[str, dict[str, float]] = field(default_factory=dict)   # sector → {ticker: exposición}


@dataclass(frozen=True)
class AccountSummary:
    """Valores de cuenta necesarios para el panel de riesgo. None = no disponible."""
    account_id: str
    net_liquidation: Optional[float] = None
    excess_liquidity: Optional[float] = None
    cushion_pct: Optional[float] = None           # Cushion de IBKR, en % (IBKR lo da como fracción)
    look_ahead_excess: Optional[float] = None
    post_expiration_excess: Optional[float] = None
    highest_severity: Optional[int] = None
    gross_position_value: Optional[float] = None
    updated_at: Optional[datetime] = None
    alias: Optional[str] = None                   # nombre de la cuenta en IBKR (tag AccountOrGroup)
    base_currency: Optional[str] = None           # moneda base de la cuenta (EUR…)
    usd_per_base: Optional[float] = None          # dólares por unidad de moneda base (EUR→USD); 1 si la cuenta es en USD
    daily_pnl: Optional[float] = None             # cambio del día en moneda base (reqPnL de IBKR)
    unrealized_pnl: Optional[float] = None        # ganancia/pérdida latente total, en moneda base
    realized_pnl: Optional[float] = None          # realizada en el día, en moneda base

    @property
    def nlv_usd(self) -> Optional[float]:
        """Valor liquidativo en USD (los strikes y posiciones están en USD). None si falta el NLV o el tipo de cambio."""
        if self.net_liquidation is None or not self.usd_per_base:
            return None
        return self.net_liquidation * self.usd_per_base


@dataclass(frozen=True)
class OptionQuote:
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    delta: Optional[float] = None
    iv: Optional[float] = None
    open_interest: Optional[int] = None
    bid_size: Optional[int] = None


@dataclass(frozen=True)
class UnderlyingQuote:
    """Precio y volatilidad implícita (30 días) actuales de un subyacente."""
    price: Optional[float] = None
    iv: Optional[float] = None


@dataclass(frozen=True)
class OptionChain:
    """Expiraciones y strikes disponibles de un subyacente."""
    ticker: str
    expiries: list[date] = field(default_factory=list)
    strikes: list[float] = field(default_factory=list)
    multiplier: int = 100


@dataclass(frozen=True)
class VixData:
    current: Optional[float] = None
    last_closes: list[tuple[date, float]] = field(default_factory=list)  # últimos N cierres
    futures: list[tuple[date, float]] = field(default_factory=list)      # (vencimiento, precio)
    updated_at: Optional[datetime] = None
