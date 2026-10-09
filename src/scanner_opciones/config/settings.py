"""Carga y validación de la configuración externa (YAML)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Literal, Optional
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
    what_if_timeout_seconds: float = Field(15, gt=0)  # tiempo máximo de un what-if de margen (TWS puede dejar de responder)
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
    batch_size: int = Field(50, ge=1)  # contratos por petición de cotizaciones a IBKR (con tastytrade manda market_data.quote_batch_size)
    account_interval_minutes: float = Field(1, gt=0)  # cada cuánto se refrescan cuenta, posiciones y VIX (independiente del mercado)
    margin_max_age_minutes: float = Field(60, ge=0)  # reutiliza el margen (what-if) guardado hasta esta edad


class DailyUpdateSettings(_Model):
    run_on_startup: bool = True
    concurrency: int = Field(4, ge=1)  # tickers que se actualizan a la vez


class CandidateRange(_Model):
    """Rango de contratos que se GUARDAN en la actualización diaria (el scanner trabaja dentro de él)."""
    strike_below_pct_min: float = Field(10, ge=0, lt=100)
    strike_below_pct_max: float = Field(30, ge=0, lt=100)
    dte_min: int = Field(1, ge=0)
    dte_max: int = Field(35, ge=1)

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


class TechnicalSettings(_Model):
    """Filtros técnicos del scanner: tendencia, zona de soporte y días desde el último toque del strike."""
    trend_pivot_width: int = Field(3, ge=1)               # días a cada lado que debe superar un máximo/mínimo local (método «máximos y mínimos»)
    trend_swings_required: int = Field(3, ge=2)           # últimos máximos y mínimos que deben ser crecientes (o decrecientes)
    trend_min_progress_pct: float = Field(5, ge=0)        # avance mínimo desde el mínimo (o máximo) sin romper
    trend_durations: tuple[int, ...] = (7, 14, 21, 30, 60, 90, 120, 180, 270, 365)   # antigüedad mínima del mínimo, en días
    trend_windows_months: tuple[int, ...] = (1, 2, 3, 6, 9, 12, 18, 24)   # ventana de la tendencia: últimos N meses de cierres
    support_lookback_days: int = Field(365, ge=30)
    support_band_pct: float = Field(1.5, gt=0)            # banda de la zona y ruptura máxima tolerada
    support_min_touches: int = Field(3, ge=2)
    support_min_clusters: int = Field(2, ge=1)
    support_cluster_gap_days: int = Field(20, ge=1)       # separación mínima entre dos toques de episodios distintos
    support_pivot_width: int = Field(3, ge=1)
    ma_slope_candles: int = Field(5, ge=1)                # pendiente de una media: se compara con su valor de hace N velas (de las elegidas: días, semanas o meses)
    chart_months: int = Field(24, ge=3, le=48)            # meses de cierres del gráfico del strike
    chart_near_pct: float = Field(5, ge=0)                # un cierre mensual a menos de este % por encima del strike se marca en amarillo
    touch_min_days_options: tuple[int, ...] = (10, 20, 30, 45, 60, 90, 120, 180, 252, 365)   # opciones de «Días mín. desde el último toque»


LEVELS = ("flexible", "standard", "strict")   # grados de exigencia, del más laxo al más estricto


def _levels(flexible: float, standard: float, strict: float) -> dict[str, float]:
    return {"flexible": flexible, "standard": standard, "strict": strict}


class SolvencyThresholds(_Model):
    """Umbral de cada filtro de solvencia / calidad del flujo de caja según el grado de exigencia elegido.
    Máximos: deuda/patrimonio, capex/flujo. Mínimos: el resto. Inspirados en los filtros de un analista (deuda/patrimonio
    ≤ 0,5, cobertura ≥ 3×, flujo operativo > 30 % de la deuda, capex < 35 % del flujo, FCF > 12 % de los activos,
    recompra neta > 2 %): «estricto» es su umbral más duro y «flexible» deja pasar a la mayoría."""
    debt_to_equity: dict[str, float] = _levels(1.5, 1.0, 0.5)          # máximo
    interest_coverage: dict[str, float] = _levels(2.0, 3.0, 5.0)       # mínimo (veces)
    cash_to_short_debt: dict[str, float] = _levels(0.5, 1.0, 2.0)      # mínimo (veces)
    ocf_to_debt: dict[str, float] = _levels(0.15, 0.30, 0.50)          # mínimo (fracción)
    capex_to_ocf: dict[str, float] = _levels(0.60, 0.35, 0.20)         # máximo (fracción)
    fcf_to_assets: dict[str, float] = _levels(0.04, 0.08, 0.12)        # mínimo (fracción)
    net_buyback_pct: dict[str, float] = _levels(0.0, 1.0, 2.0)         # mínimo (% de reducción del nº de acciones)

    @model_validator(mode="after")
    def _check(self) -> "SolvencyThresholds":
        for name in type(self).model_fields:
            if set(getattr(self, name)) != set(LEVELS):
                raise ValueError(f"{name} debe definir exactamente los grados {', '.join(LEVELS)}")
        return self


class QualitySettings(_Model):
    """Filtros de calidad de la empresa del scanner (beneficios, trimestres, liquidez, resultados)."""
    liquidity_options: list[int] = [2, 3, 4]               # liquidez mínima de las opciones (1-5, de tastytrade)
    positive_quarters_options: list[int] = [2, 3, 4]       # trimestres con beneficios exigidos de los últimos 4
    leverage_options: list[float] = [1, 2, 3, 5]           # pasivo/patrimonio máximo ofrecido (no aplica a las financieras)
    refresh_days: int = Field(7, ge=1)                     # cada cuántos días se vuelve a bajar el historial trimestral
    thresholds: SolvencyThresholds = SolvencyThresholds()  # umbrales de los filtros de solvencia por grado de exigencia
    level_labels: dict[str, str] = {"flexible": "Flexible", "standard": "Estándar", "strict": "Estricto"}
    # sectores a los que NO se miden la deuda, la caja ni la solvencia (pasan sin medirse): trozos del nombre del sector
    exempt_sectors: list[str] = ["financ", "energy", "utilit", "material", "real estate"]


class ScannerSettings(_Model):
    price_reference: PriceReferenceSettings = PriceReferenceSettings()
    candidates: CandidateRange = CandidateRange()
    # puntos porcentuales que se guardan de más por encima y por debajo de `candidates` (en strikes): así el
    # rango cotizado puede seguir al precio del subyacente sin pedir contratos nuevos a cada oscilación
    catalog_margin_pct: float = Field(5, ge=0, lt=100)
    initial: InitialFilterSettings = InitialFilterSettings()
    filters: FilterSettings = FilterSettings()
    filter_values: FilterValues = FilterValues()
    presets: tuple[ScannerPreset, ...] = DEFAULT_PRESETS
    technical: TechnicalSettings = TechnicalSettings()
    quality: QualitySettings = QualitySettings()

    @property
    def catalog(self) -> CandidateRange:
        """Rango de contratos que se GUARDAN: `candidates` ampliado con `catalog_margin_pct` en los strikes."""
        c = self.candidates
        return CandidateRange(
            strike_below_pct_min=max(0.0, c.strike_below_pct_min - self.catalog_margin_pct),
            strike_below_pct_max=min(99.0, c.strike_below_pct_max + self.catalog_margin_pct),
            dte_min=c.dte_min, dte_max=c.dte_max,
        )


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


class MarketDataSettings(_Model):
    """De dónde salen la cadena, las cotizaciones de opciones, los precios y los ex-dividendos. La cuenta, las
    posiciones, el margen what-if, el sector y el VIX son siempre de IBKR."""
    source: Literal["tastytrade", "ibkr"] = "tastytrade"   # «ibkr» recupera el comportamiento anterior (todo por TWS)
    quote_wait_seconds: float = Field(20, gt=0)   # espera máxima por lote de cotizaciones DXLink
    settle_seconds: float = Field(3, gt=0)        # sin datos nuevos durante tanto tiempo, el lote se da por terminado
    quote_batch_size: int = Field(2500, ge=1, le=3000)  # contratos por conexión DXLink (probado: 3000 sí, 4000 no) y por petición de cotizaciones
    listing_ttl_minutes: float = Field(30, gt=0)  # cuánto se reutiliza la lista de contratos de un ticker


class EdgarSettings(_Model):
    """SEC EDGAR (balance y flujos de caja): la SEC exige un contacto en el User-Agent; sin él la función queda apagada."""
    contact: str = ""                                    # tu correo (o nombre y correo): se envía a la SEC en el User-Agent
    refresh_days: int = Field(14, ge=1)                  # cada cuántos días se consulta de nuevo (los informes son trimestrales)
    requests_per_second: float = Field(5, gt=0, le=10)   # la SEC permite 10 como máximo


class TrendSettings(_Model):
    """Histórico de cierres diarios de tastytrade (DXLink) en el que se basan los filtros técnicos del scanner."""
    history_days: int = Field(1400, ge=30)          # días naturales de cierres que se guardan (~3,8 años: lo máximo que da tastytrade)
    batch_timeout_seconds: float = Field(20, gt=0)  # espera máxima por lote de 50 tickers



class StorageSettings(_Model):
    path: str = "data/app.db"


class LoggingSettings(_Model):
    level: str = "INFO"
    ib_async_level: str = "WARNING"  # nivel del log de ib_async (INFO escribe cada updatePortfolio)
    tastytrade_level: str = "WARNING"  # nivel del log del SDK de tastytrade (se pone solo en DEBUG y escribe cada mensaje DXLink)
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
    market_data: MarketDataSettings = MarketDataSettings()
    edgar: EdgarSettings = EdgarSettings()
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
