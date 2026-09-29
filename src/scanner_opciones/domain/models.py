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
    updated_at: datetime
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


@dataclass(frozen=True)
class Position:
    """Acción (`option is None`) o opción abierta."""
    ticker: str
    quantity: float
    market_value: float
    sector: Optional[str] = None
    option: Optional[OptionContract] = None


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
