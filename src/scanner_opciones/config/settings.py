"""Carga y validación de la configuración externa (YAML)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from scanner_opciones.domain.enums import AccountMode, PriceReference
from scanner_opciones.domain.errors import ConfigError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ports(_Model):
    live: int = Field(7496, ge=1, le=65535)
    paper: int = Field(7497, ge=1, le=65535)


class Accounts(_Model):
    """Id de cuenta IBKR por modo (None = la primera que gestione TWS)."""
    live: Optional[str] = None
    paper: Optional[str] = None


class AccountTags(_Model):
    """Nombres de los valores de cuenta de IBKR (verificados contra TWS el 2026-09-29)."""
    net_liquidation: str = "NetLiquidation"
    excess_liquidity: str = "ExcessLiquidity"
    cushion: str = "Cushion"                    # fracción (0.99 = 99 %)
    look_ahead_excess: str = "LookAheadExcessLiquidity"
    post_expiration_excess: str = "PostExpirationExcess"
    gross_position_value: str = "GrossPositionValue"
    highest_severity: str = "HighestSeverity"   # 0 normal, 1 aviso, 2 elevado, 3 crítico


class IbkrSettings(_Model):
    host: str = "127.0.0.1"
    ports: Ports = Ports()
    client_id: int = 1
    accounts: Accounts = Accounts()  # imprescindible en el modo cuyo usuario gestione varias cuentas
    auto_detect_mode: bool = True    # al arrancar, elige live/paper según el puerto que responda
    mode: AccountMode = AccountMode.PAPER
    market_data_type: int = Field(2, ge=1, le=4)  # 2 = congelado: live, y con el mercado cerrado el último cierre
    delayed_minutes: float = Field(15, ge=0)  # retraso de los datos diferidos (tipos 3 y 4)
    connect_timeout_seconds: float = Field(10, gt=0)
    quote_wait_seconds: float = Field(4, gt=0)  # espera de ticks tras pedir cotizaciones
    historical_requests_per_10min: int = Field(50, ge=1)  # límite de pacing de IBKR: 60
    account_tags: AccountTags = AccountTags()

    @property
    def account(self) -> Optional[str]:
        return self.accounts.live if self.mode is AccountMode.LIVE else self.accounts.paper

    @property
    def port(self) -> int:
        return self.ports.live if self.mode is AccountMode.LIVE else self.ports.paper

    @property
    def delay_minutes(self) -> float:
        """Retraso de los datos de mercado: `delayed_minutes` con datos diferidos (3/4), 0 si no."""
        return self.delayed_minutes if self.market_data_type in (3, 4) else 0.0


class RefreshSettings(_Model):
    interval_minutes: float = Field(5, gt=0)
    batch_size: int = Field(50, ge=1)  # contratos por petición de cotizaciones
    margin_max_age_minutes: float = Field(60, ge=0)  # reutiliza el margen (what-if) guardado hasta esta edad


class DailyUpdateSettings(_Model):
    run_on_startup: bool = True
    concurrency: int = Field(4, ge=1)  # tickers que se actualizan a la vez


class CandidateRange(_Model):
    """Rango de contratos que se GUARDAN en la actualización diaria (el scanner trabaja dentro de él)."""
    strike_below_pct_min: float = Field(5, ge=0, lt=100)
    strike_below_pct_max: float = Field(35, ge=0, lt=100)
    dte_min: int = Field(1, ge=0)
    dte_max: int = Field(45, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "CandidateRange":
        if self.strike_below_pct_min > self.strike_below_pct_max:
            raise ValueError("strike_below_pct_min no puede superar strike_below_pct_max")
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min no puede superar dte_max")
        return self


class InitialFilterSettings(_Model):
    """Valores iniciales del filtro del scanner (todos editables en el formulario)."""
    strike_below_pct_min: float = Field(10, ge=0, lt=100)  # descuento mínimo del strike (%)
    strike_below_pct_max: float = Field(30, ge=0, lt=100)  # descuento máximo del strike (%)
    min_annual_yield_pct: float = Field(12.0, ge=0)  # yield anualizado mínimo (≈ 1 % bruto a 30 días)
    dte_min: int = Field(1, ge=0)
    dte_max: int = Field(35, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "InitialFilterSettings":
        if self.strike_below_pct_min > self.strike_below_pct_max:
            raise ValueError("strike_below_pct_min no puede superar strike_below_pct_max")
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min no puede superar dte_max")
        return self


class ScannerPreset(_Model):
    """Botón del scanner que carga estos valores en el formulario y escanea. `dte_max` None = el máximo
    de la ventana guardada (`candidates.dte_max`)."""
    name: str
    strike_below_pct_min: float = Field(ge=0, lt=100)
    dte_min: int = Field(ge=0)
    dte_max: Optional[int] = Field(None, ge=0)
    min_annual_yield_pct: float = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "ScannerPreset":
        if self.dte_max is not None and self.dte_min > self.dte_max:
            raise ValueError("dte_min no puede superar dte_max")
        return self


DEFAULT_PRESETS = (
    ScannerPreset(name="Corto plazo", strike_below_pct_min=10, dte_min=1, dte_max=15, min_annual_yield_pct=20),
    ScannerPreset(name="Largo plazo", strike_below_pct_min=20, dte_min=16, dte_max=None, min_annual_yield_pct=13),
)


class FilterSettings(_Model):
    min_oi: Optional[int] = Field(None, ge=0)
    min_bid_size: Optional[int] = Field(None, ge=0)
    max_spread_pct: Optional[float] = Field(None, ge=0)
    min_iv_rank: Optional[float] = Field(None, ge=0, le=100)
    min_iv_percentile: Optional[float] = Field(None, ge=0, le=100)
    only_uptrend: bool = False   # solo tickers con precio > media corta > media larga (`trend`)


class FilterValues(_Model):
    """Valores que aparecen en las cajas de los filtros opcionales aunque estén desmarcados
    (marcar el filtro los aplica). `scanner.filters` decide cuáles empiezan marcados."""
    min_oi: int = Field(100, ge=0)
    min_bid_size: int = Field(20, ge=0)
    max_spread_pct: float = Field(35, ge=0)
    min_iv_rank: float = Field(30, ge=0, le=100)
    min_iv_percentile: float = Field(50, ge=0, le=100)


class PriceReferenceSettings(_Model):
    """Precio de venta de referencia para el yield (editable en el formulario del scanner)."""
    mode: PriceReference = PriceReference.BID_PLUS_SPREAD
    spread_pct: float = Field(25, ge=0, le=100)  # X: % del spread que se suma al bid (0 = bid, 50 = mid)


class FrameSettings(_Model):
    """Análisis de máximos y mínimos crecientes en una ventana (diaria, semanal o mensual)."""
    pivot_width: int = Field(3, ge=1)       # barras a cada lado que debe superar un máximo/mínimo local
    lookback_bars: int = Field(120, ge=5)   # barras de la ventana que se analizan
    swings_required: int = Field(3, ge=2)   # últimos máximos y mínimos que deben ser crecientes (o decrecientes)


class TechnicalSettings(_Model):
    """Filtros técnicos del scanner: tendencia, zona de soporte y días desde el último toque del strike."""
    daily: FrameSettings = FrameSettings(pivot_width=3, lookback_bars=120, swings_required=3)
    weekly: FrameSettings = FrameSettings(pivot_width=2, lookback_bars=52, swings_required=3)
    monthly: FrameSettings = FrameSettings(pivot_width=1, lookback_bars=24, swings_required=2)
    trend_min_progress_pct: float = Field(5, ge=0)        # avance mínimo desde el mínimo (o máximo) sin romper
    trend_durations: tuple[int, ...] = (7, 14, 30, 60, 90, 180, 365)   # antigüedad mínima del mínimo, en días
    support_lookback_days: int = Field(365, ge=30)
    support_band_pct: float = Field(1.5, gt=0)            # banda de la zona y ruptura máxima tolerada
    support_min_touches: int = Field(3, ge=2)
    support_min_clusters: int = Field(2, ge=1)
    support_cluster_gap_days: int = Field(20, ge=1)       # separación mínima entre dos toques de episodios distintos
    support_pivot_width: int = Field(3, ge=1)
    touch_min_days_options: tuple[int, ...] = (7, 14, 30, 60, 90, 180, 365)   # opciones de «Días mín. desde el último toque»

    def frame(self, name: str) -> FrameSettings:
        return getattr(self, name)


class ScannerSettings(_Model):
    price_reference: PriceReferenceSettings = PriceReferenceSettings()
    candidates: CandidateRange = CandidateRange()
    initial: InitialFilterSettings = InitialFilterSettings()
    filters: FilterSettings = FilterSettings()
    filter_values: FilterValues = FilterValues()
    presets: tuple[ScannerPreset, ...] = DEFAULT_PRESETS
    technical: TechnicalSettings = TechnicalSettings()


class CushionThresholds(_Model):
    normal_above: float = Field(40, ge=0, le=100)
    concern_above: float = Field(30, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "CushionThresholds":
        if self.concern_above >= self.normal_above:
            raise ValueError("concern_above debe ser menor que normal_above")
        return self


class RiskSettings(_Model):
    cushion_thresholds: CushionThresholds = CushionThresholds()


class DiversificationSettings(_Model):
    weeks_ahead: int = Field(5, ge=1)


class VixSettings(_Model):
    history_days: int = Field(5, ge=1)
    futures_ahead: int = Field(3, ge=1)


class TastytradeSettings(_Model):
    """Credenciales OAuth de tastytrade (solo lectura): de ahí salen IV Rank e IV Percentile. No se muestran en repr/logs."""
    client_secret: Optional[str] = Field(None, repr=False)
    refresh_token: Optional[str] = Field(None, repr=False)
    # si el precio de IBKR se aleja más de este % del de tastytrade, se usa el de tastytrade (precio extraño)
    price_max_deviation_pct: float = Field(5, gt=0)


class TrendSettings(_Model):
    """Tendencia alcista: precio > media de `sma_short` cierres > media de `sma_long` cierres (velas diarias de tastytrade)."""
    sma_short: int = Field(50, ge=2)
    sma_long: int = Field(200, ge=3)
    history_days: int = Field(730, ge=30)           # días naturales de cierres que se guardan (~2 años)
    batch_timeout_seconds: float = Field(20, gt=0)  # espera máxima por lote de 50 tickers

    @model_validator(mode="after")
    def _check(self) -> "TrendSettings":
        if self.sma_short >= self.sma_long:
            raise ValueError("sma_short debe ser menor que sma_long")
        return self


class StorageSettings(_Model):
    path: str = "data/app.db"


class LoggingSettings(_Model):
    level: str = "INFO"
    ib_async_level: str = "WARNING"  # nivel del log de ib_async (INFO escribe cada updatePortfolio)
    file: Optional[str] = "logs/scanner.log"  # fichero de log con rotación; null = solo consola
    file_max_mb: int = Field(5, ge=1)
    file_backups: int = Field(3, ge=0)


class MarketSettings(_Model):
    """Horario del mercado de opciones: fuera de él el refresco automático no cotiza (no hay datos nuevos)."""
    timezone: str = "America/New_York"
    open: time = time(9, 30)        # entre comillas en el YAML ("09:30"): sin ellas YAML lo lee como número
    close: time = time(16, 0)
    holidays: list[date] = Field(default_factory=list)  # festivos de EE. UU. (se mantienen a mano)
    pause_when_closed: bool = True  # False: refrescar siempre, como antes

    @model_validator(mode="after")
    def _check(self) -> "MarketSettings":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"zona horaria desconocida: {self.timezone}") from exc
        if self.open >= self.close:
            raise ValueError("market.open debe ser anterior a market.close")
        return self


class Settings(_Model):
    ibkr: IbkrSettings = IbkrSettings()
    refresh: RefreshSettings = RefreshSettings()
    daily_update: DailyUpdateSettings = DailyUpdateSettings()
    scanner: ScannerSettings = ScannerSettings()
    risk: RiskSettings = RiskSettings()
    diversification: DiversificationSettings = DiversificationSettings()
    vix: VixSettings = VixSettings()
    tastytrade: TastytradeSettings = TastytradeSettings()
    trend: TrendSettings = TrendSettings()
    storage: StorageSettings = StorageSettings()
    logging: LoggingSettings = LoggingSettings()
    market: MarketSettings = MarketSettings()

    @property
    def refresh_interval_minutes(self) -> float:
        """Intervalo real del refresco: con datos diferidos no tiene sentido pedir más a menudo
        que el retraso (las cotizaciones no habrían cambiado)."""
        return max(self.refresh.interval_minutes, self.ibkr.delay_minutes)


def load_settings(path: str | Path) -> Settings:
    """Carga `path` (YAML). Lanza ConfigError con un mensaje legible si es inválido."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"No existe el fichero de configuración: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido en {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} debe contener un mapa YAML en la raíz")
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        detalles = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Configuración inválida en {p}: {detalles}") from exc
