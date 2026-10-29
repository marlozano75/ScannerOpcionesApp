"""Carga y validación de la configuración externa (YAML)."""
from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from scanner_opciones.domain.enums import AccountMode, Platform, PriceReference
from scanner_opciones.domain.errors import ConfigError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ports(_Model):
    live: int = Field(7496, ge=1, le=65535)             # TWS real
    paper: int = Field(7497, ge=1, le=65535)            # TWS simulada
    gateway_live: int = Field(4001, ge=1, le=65535)     # IB Gateway real (puertos por defecto del Gateway)
    gateway_paper: int = Field(4002, ge=1, le=65535)    # IB Gateway simulada

    def get(self, platform: Platform, mode: AccountMode) -> int:
        if platform is Platform.GATEWAY:
            return self.gateway_live if mode is AccountMode.LIVE else self.gateway_paper
        return self.live if mode is AccountMode.LIVE else self.paper


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
    auto_detect_mode: bool = True    # al arrancar, elige plataforma (TWS/Gateway) y live/paper según el puerto que responda
    platform: Platform = Platform.TWS   # TWS o IB Gateway (cada uno con sus puertos)
    mode: AccountMode = AccountMode.PAPER
    market_data_type: int = Field(2, ge=1, le=4)  # 2 = congelado: live, y con el mercado cerrado el último cierre
    delayed_minutes: float = Field(15, ge=0)  # retraso de los datos diferidos (tipos 3 y 4)
    connect_timeout_seconds: float = Field(10, gt=0)
    reconnect_interval_seconds: float = Field(30, gt=0)  # cada cuánto se reintenta la conexión con TWS si está caída
    quote_wait_seconds: float = Field(4, gt=0)  # espera de ticks tras pedir cotizaciones
    what_if_timeout_seconds: float = Field(15, gt=0)  # tiempo máximo de un what-if de margen (TWS puede dejar de responder)
    historical_requests_per_10min: int = Field(50, ge=1)  # límite de pacing de IBKR: 60
    instruments_market_data_type: int = Field(3, ge=1, le=4)   # datos de los instrumentos (`instruments`, p. ej. Eurex) sin suscripción de la bolsa: diferidos (3). Tienen su propia conexión de TWS
    instruments_client_offset: int = Field(10, ge=1)     # ... con el clientId de la principal más este desplazamiento
    account_tags: AccountTags = AccountTags()

    @property
    def account(self) -> Optional[str]:
        return self.accounts.live if self.mode is AccountMode.LIVE else self.accounts.paper

    @property
    def port(self) -> int:
        return self.ports.get(self.platform, self.mode)

    @property
    def delay_minutes(self) -> float:
        """Retraso de los datos de mercado: `delayed_minutes` con datos diferidos (3/4), 0 si no."""
        return self.delayed_minutes if self.market_data_type in (3, 4) else 0.0


class InstrumentSettings(_Model):
    """Subyacente que NO es una acción de EE. UU. en USD (p. ej. un índice europeo): su cadena, cotizaciones, precio y margen salen de TWS (tastytrade no
    lo cubre) y los cierres diarios de Yahoo. La clave del mapa `Settings.instruments` es el ticker de la app."""
    kind: Literal["index"] = "index"
    symbol: str                                  # símbolo en IBKR («ESTX50»)
    exchange: str                                # bolsa del subyacente y de sus opciones («EUREX»)
    currency: str                                # moneda de strikes, primas y margen («EUR»)
    multiplier: int = Field(10, ge=1)            # multiplicador de las opciones
    trading_class: str                           # clase de las opciones de la cadena de IBKR («OESX»: mensuales, semanales y de fin de mes)
    yahoo_symbol: Optional[str] = None           # símbolo de Yahoo para los cierres diarios; None = sin histórico
    sector: str = "Índice"                       # IBKR no da sector a los índices
    spread_widths: tuple[float, ...] = ()        # anchos de los spreads en puntos (los de `scanner.spreads.width_options` son de acciones: 1–20 $); vacío = esos
    name: Optional[str] = None
    # Horario de SU mercado (con él se cotiza mientras está abierto aunque el de EE. UU. esté cerrado); lunes–viernes, sin festivos propios
    timezone: str = "Europe/Berlin"
    open: time = time(8, 0)                      # entre comillas en el YAML ("08:00")
    close: time = time(22, 0)

    @model_validator(mode="after")
    def _check_hours(self) -> "InstrumentSettings":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"zona horaria desconocida: {self.timezone}") from exc
        if self.open >= self.close:
            raise ValueError("el horario del instrumento: open debe ser anterior a close")
        return self


def default_instruments() -> dict[str, InstrumentSettings]:
    return {"ESTX50": InstrumentSettings(symbol="ESTX50", exchange="EUREX", currency="EUR", multiplier=10, trading_class="OESX",
                                         yahoo_symbol="^STOXX50E", sector="Índice Europa", name="Euro Stoxx 50",
                                         spread_widths=(50.0, 100.0, 150.0, 200.0, 250.0, 300.0))}


class RefreshSettings(_Model):
    interval_minutes: float = Field(5, gt=0)
    batch_size: int = Field(50, ge=1)  # contratos por petición de cotizaciones a IBKR (con tastytrade manda market_data.quote_batch_size)
    account_interval_minutes: float = Field(1, gt=0)  # cada cuánto se refrescan cuenta, posiciones y VIX (independiente del mercado)
    margin_max_age_minutes: float = Field(60, ge=0)  # reutiliza el margen (what-if) guardado hasta esta edad
    margin_concurrency: int = Field(8, ge=1)  # what-if que se piden a TWS a la vez (el primero de cada ciclo va solo, como sonda)
    # El bid size de tastytrade es entre 5 y 10 veces menor que el de TWS. Con el mercado abierto y TWS conectado, el refresco pide a TWS
    # el de los contratos que pasan los demás filtros pero tienen un tamaño menor que `scanner.filter_values.min_bid_size`.
    tws_bid_size: bool = True
    tws_bid_size_max_age_minutes: float = Field(15, ge=0)   # un tamaño de TWS se reutiliza hasta esta edad antes de pedirlo otra vez
    tws_bid_size_max_contracts: int = Field(600, ge=1)      # tope por ciclo (los de mayor yield anualizado primero): ~4 s por cada 50


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
    min_yield_pct: float = Field(1.0, ge=0)  # yield mínimo del PERIODO (prima / strike hasta el vencimiento, sin anualizar; el 1 % de TPC)
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
    min_yield_pct: float = Field(ge=0)
    min_credit_usd: Optional[float] = Field(30, gt=0)   # crédito mínimo de la put, en $ por contrato (None = sin mínimo)

    @model_validator(mode="after")
    def _check(self) -> "ScannerPreset":
        if self.dte_max is not None and self.dte_min > self.dte_max:
            raise ValueError("dte_min no puede superar dte_max")
        return self


# Perfiles del método The Premium Collector (TPC): prima mínima del 1 % del strike hasta el vencimiento (yield del periodo, sin anualizar).
#   Mensual: ~30 DTE, strike 20–25 % por debajo.  Táctico: menos de 15 DTE, strike ~10 % por debajo (cuando el mercado cae).
DEFAULT_PRESETS = (
    ScannerPreset(name="Mensual (TPC)", strike_below_pct_min=20, dte_min=25, dte_max=35, min_yield_pct=1.0),
    ScannerPreset(name="Táctico (TPC)", strike_below_pct_min=10, dte_min=10, dte_max=14, min_yield_pct=1.0),
)


class FilterSettings(_Model):
    min_oi: Optional[int] = Field(None, ge=0)
    min_bid_size: Optional[int] = Field(None, ge=0)
    max_spread_pct: Optional[float] = Field(None, ge=0)
    min_iv_rank: Optional[float] = Field(None, ge=0, le=100)
    min_iv_percentile: Optional[float] = Field(None, ge=0, le=100)
    min_put_credit: Optional[float] = Field(None, gt=0)
    max_capital_risk_pct: Optional[float] = Field(None, gt=0, le=100)


class FilterValues(_Model):
    """Valores que aparecen en las cajas de los filtros opcionales aunque estén desmarcados
    (marcar el filtro los aplica). `scanner.filters` decide cuáles empiezan marcados."""
    min_oi: int = Field(100, ge=0)
    min_bid_size: int = Field(20, ge=0)
    max_spread_pct: float = Field(35, ge=0)
    min_iv_rank: float = Field(30, ge=0, le=100)
    min_iv_percentile: float = Field(50, ge=0, le=100)
    min_put_credit: float = Field(30, gt=0)
    max_capital_risk_pct: float = Field(10, gt=0, le=100)


class SpreadSettings(_Model):
    """Modo «Spread» del scanner (put vendida + put comprada del mismo vencimiento con strike inferior): anchos
    probados siempre todos (en $) y valores que aparecen en las cajas de los filtros del spread."""
    width_options: tuple[float, ...] = (1.0, 2.5, 5.0, 10.0, 15.0, 20.0)
    min_return_on_risk_pct: float = Field(25, gt=0)   # retorno mínimo del PERIODO sobre el riesgo: crédito / (ancho − crédito), sin anualizar (como el yield de TPC)
    min_long_oi: int = Field(10, ge=0)               # OI mínimo de la pata comprada
    max_long_spread_pct: float = Field(25, gt=0)     # spread bid/ask máximo de la pata comprada (%); sin bid no pasa
    max_combo_spread_pct: float = Field(15, gt=0)    # spread bid/ask del combo (vendida − comprada) máximo, en % del crédito: con un combo ancho el crédito es incierto
    min_credit_usd: float = Field(30, gt=0)          # crédito neto mínimo por spread, en $ por contrato (descarta spreads de céntimos)
    # criterios de Tom Sosnoff (tastylive): vendida a 16-20 Δ (el filtro de delta de la put vendida), comprada lo más alejada posible (≈ 10 Δ) o con un
    # ancho fijo de 5 o 10 $, y crédito neto del 30-35 % del ancho. Cada uno se marca aparte en el juego de filtros de Spread (empiezan desmarcados)
    max_long_abs_delta: float = Field(0.10, gt=0, le=1)       # |delta| máximo de la put comprada
    min_credit_width_pct: float = Field(30, gt=0, lt=100)     # crédito neto / ancho mínimo (%)
    max_credit_width_pct: Optional[float] = Field(35, gt=0, lt=100)   # ... y máximo (%; vacío = sin tope)
    fixed_widths: tuple[float, ...] = (5.0, 10.0)             # anchos permitidos cuando se marca «Anchos fijos» (en lugar de probarlos todos)
    sosnoff_delta_min: float = Field(0.16, gt=0, le=1)        # |delta| de la put vendida que carga el botón «Aplicar criterios de Sosnoff»
    sosnoff_delta_max: float = Field(0.20, gt=0, le=1)
    # modo «Ambos»: la put desnuda solo se ofrece si cabe en este límite; si no, sale el spread
    max_naked_strike: float = Field(100, gt=0)       # strike máximo de la put desnuda ($)
    max_loss_nlv_pct: float = Field(2, gt=0, le=100)  # pérdida máxima del spread como % del NLV
    usd_per_base: Optional[float] = Field(None, gt=0)  # dólares por unidad de moneda base si IBKR no da el tipo de cambio

    @model_validator(mode="after")
    def _check(self) -> "SpreadSettings":
        if not self.width_options or any(w <= 0 for w in self.width_options):
            raise ValueError("width_options debe tener anchos positivos")
        if not self.fixed_widths or any(w <= 0 for w in self.fixed_widths):
            raise ValueError("fixed_widths debe tener anchos positivos")
        if self.max_credit_width_pct is not None and self.max_credit_width_pct < self.min_credit_width_pct:
            raise ValueError("max_credit_width_pct no puede ser menor que min_credit_width_pct")
        if self.sosnoff_delta_max < self.sosnoff_delta_min:
            raise ValueError("sosnoff_delta_max no puede ser menor que sosnoff_delta_min")
        return self


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
    rsi_period: int = Field(14, ge=2)                      # RSI de Wilder
    rsi_lookback_days: int = Field(10, ge=2)               # sesiones en las que el RSI debe haber tocado el nivel («rebote»)
    hv_period: int = Field(30, ge=5)                       # sesiones de la HV30 propia (respaldo si tastytrade no la da)
    atr_period: int = Field(14, ge=2)                      # ATR de Wilder (máximos, mínimos y cierres diarios)
    bollinger_period: int = Field(20, ge=2)
    bollinger_k: float = Field(2.0, gt=0)                  # desviaciones típicas de las bandas
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
    # ROIC alto = beneficios más fiables (el vídeo no da umbral; Meta ≈ 23 %, Microsoft ≈ 29 %): estos tres son una suposición
    roic: dict[str, float] = _levels(0.08, 0.12, 0.20)                 # mínimo (fracción)
    loss_years: dict[str, float] = _levels(2, 1, 0)                    # máximo de años con pérdidas de los últimos 10
    revenue_drops: dict[str, float] = _levels(2, 1, 0)                 # máximo de años con caída de ingresos de los últimos 10
    # desviación típica del crecimiento anual del beneficio neto (fracción); el vídeo no da umbral: SUPOSICIÓN calibrada con EDGAR
    earnings_volatility: dict[str, float] = _levels(0.80, 0.50, 0.30)  # máximo (dejan pasar ~75 %, ~40 % y ~20 % de la watchlist)

    @model_validator(mode="after")
    def _check(self) -> "SolvencyThresholds":
        for name in type(self).model_fields:
            if set(getattr(self, name)) != set(LEVELS):
                raise ValueError(f"{name} debe definir exactamente los grados {', '.join(LEVELS)}")
        return self


class ManageableDebt(_Model):
    """«Deuda baja o manejable»: pasa la empresa con poca deuda (deuda/patrimonio ≤ máximo) o con la deuda bien cubierta
    (resultado operativo / intereses ≥ mínimo). Solo se descarta la deuda alta Y mal cubierta."""
    max_debt_to_equity: float = Field(0.5, ge=0)
    min_interest_coverage: float = Field(3.0, ge=0)


class QualityPreset(_Model):
    """Botón del scanner que rellena los filtros de calidad (y escanea). `levels`: parámetro del formulario ->
    grado de exigencia (q_de, q_cov, q_cash, q_ocfd, q_capex, q_fcfa, q_bb)."""
    name: str
    levels: dict[str, str] = {}
    manageable_debt: bool = False

    @model_validator(mode="after")
    def _check(self) -> "QualityPreset":
        unknown = set(self.levels) - {"q_de", "q_cov", "q_cash", "q_ocfd", "q_capex", "q_fcfa", "q_bb", "q_roic", "q_loss", "q_revdrop", "q_evol"}
        if unknown:
            raise ValueError(f"filtros desconocidos en el preset «{self.name}»: {', '.join(sorted(unknown))}")
        if bad := set(self.levels.values()) - set(LEVELS):
            raise ValueError(f"grados desconocidos en el preset «{self.name}»: {', '.join(sorted(bad))}")
        return self


# Los filtros del vídeo de un analista: los cuatro de caja (flujo operativo > 30 % de la deuda, capex < 35 % del flujo,
# recompra neta > 2 %, FCF > 12 % de los activos) y los tres de deuda (deuda/patrimonio < 1, efectivo ≥ deuda a corto plazo,
# cobertura de intereses ≥ 3).
DEFAULT_QUALITY_PRESETS = (
    QualityPreset(name="Caja (4 filtros)", levels={"q_ocfd": "standard", "q_capex": "standard", "q_bb": "strict", "q_fcfa": "strict"}),
    QualityPreset(name="Deuda sana", levels={"q_de": "standard", "q_cash": "standard", "q_cov": "standard"}),
)


class PutFloor(_Model):
    """Calidad mínima que se exige SIEMPRE a una put desnuda, sea cual sea su origen (watchlist, fuentes, radar de Tortuga...). Quien no la
    cumple solo se escanea como spread. Un dato que falta
    no cumple; los ETF e índices y los sectores exentos de `QualitySettings` se miden como en el Universo. Sin `edgar.contact` no hay FCF y
    ese requisito se ignora (si no, ninguna acción pasaría)."""
    enabled: bool = True
    require_profitable: bool = True                     # beneficios (EPS) de los últimos 12 meses > 0
    min_positive_quarters: int = Field(3, ge=0, le=4)   # trimestres con beneficios de los últimos 4 (0 = no se exige)
    require_positive_fcf: bool = True                   # flujo de caja libre de 12 meses > 0 (las financieras y sectores exentos no se miden)


class QualitySettings(_Model):
    """Filtros de calidad de la empresa del scanner (beneficios, trimestres, liquidez, resultados, solvencia)."""
    liquidity_options: list[int] = [2, 3, 4]               # liquidez mínima de las opciones (1-5, de tastytrade)
    positive_quarters_options: list[int] = [2, 3, 4]       # trimestres con beneficios exigidos de los últimos 4
    leverage_options: list[float] = [1, 2, 3, 5]           # pasivo/patrimonio máximo ofrecido (no aplica a las financieras)
    refresh_days: int = Field(7, ge=1)                     # cada cuántos días se vuelve a bajar el historial trimestral
    metrics_ready_utc: str = Field("10:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # hora UTC a la que tastytrade termina su recálculo diario
                                                            # de las market metrics (medido: 10:00-10:10 UTC); antes de la siguiente no se piden de nuevo
    thresholds: SolvencyThresholds = SolvencyThresholds()  # umbrales de los filtros de solvencia por grado de exigencia
    manageable_debt: ManageableDebt = ManageableDebt()     # umbrales del filtro «deuda baja o manejable»
    put_floor: PutFloor = PutFloor()                       # calidad mínima de toda put desnuda del Scanner
    presets: tuple[QualityPreset, ...] = DEFAULT_QUALITY_PRESETS   # botones que rellenan los filtros (ninguno activo por defecto)
    level_labels: dict[str, str] = {"flexible": "Flexible", "standard": "Estándar", "strict": "Estricto"}
    # sectores a los que NO se miden la deuda, la caja ni la solvencia (pasan sin medirse): trozos del nombre del sector
    exempt_sectors: list[str] = ["financ", "energy", "utilit", "material", "real estate"]
    # ETF e índices: no tienen EPS ni datos en EDGAR, así que no se les exige ningún filtro de la empresa (sí la liquidez de opciones)
    etf_index_symbols: list[str] = [
        "SPY", "QQQ", "IWM", "DIA", "XSP", "SMH", "GLD", "SLV", "TLT", "HYG", "USO", "IBIT", "EEM", "EFA", "FXI", "EWZ", "GDX", "XBI", "KRE",
        "XLE", "XLF", "XLK", "XLV", "XLU", "XLI", "XLP", "XLY", "XLC", "XLB", "XLRE",
        "TQQQ", "SQQQ", "VXX", "KWEB",      # apalancados / volatilidad / China
        "FEZ", "EZU", "VGK",                # Euro Stoxx 50, eurozona y Europa (ETF en dólares con opciones en EE. UU.)
        "ESTX50",                           # Euro Stoxx 50 (índice de Eurex en euros, ver `instruments`)
    ]

    @property
    def exempt_entries(self) -> tuple[str, ...]:
        """Lo que entiende `scanner.quality.is_exempt`: trozos de sector y, con el prefijo «=», tickers exactos (los ETF)."""
        return (*self.exempt_sectors, *(f"={s.upper()}" for s in self.etf_index_symbols))


class StrategySettings(_Model):
    """Perfil «Tastytrade» (venta de volatilidad: IV Rank alto, delta ≤ 0,30, 30–45 DTE, gestión al 50 %/21 DTE) y, a la vez, la estrategia
    de venta de puts «tras una caída que se calma»: valores que carga el botón del scanner (activa de una
    vez todos los filtros relacionados) y valores que muestran por defecto las cajas de delta y cercanía a
    un nivel. Orientativos, no reglas exactas. Solo se escanean los contratos guardados: para tener DTE 30–45 y strikes cercanos,
    `scanner.candidates` tiene que cubrir ese rango (`dte_max` ≥ `dte_max` de aquí y `strike_below_pct_min` ≤ el de aquí)."""
    strike_below_pct_min: float = Field(5, ge=0, lt=100)   # descuento mínimo del strike (%)
    dte_min: int = Field(30, ge=0)
    dte_max: int = Field(45, ge=0)
    min_yield_pct: float = Field(1.0, ge=0)         # prima mínima del periodo (% del strike hasta el vencimiento)
    min_credit_usd: Optional[float] = Field(30, gt=0)   # crédito mínimo de la put, en $ por contrato (None = sin mínimo): las primas de céntimos se comen las comisiones
    # |delta| de la put: solo MÁXIMO por defecto (≤ 0,30 ≈ 70 % o más de probabilidad de expirar sin valor). El mínimo de prima lo pone el
    # yield mínimo; con un mínimo de delta (0,20) se descartaba el 92 % de los contratos y sale casi todo lo más seguro
    min_abs_delta: Optional[float] = Field(None, gt=0, le=1)
    max_abs_delta: float = Field(0.30, gt=0, le=1)
    min_iv_rank: float = Field(50, ge=0, le=100)           # > 30 aceptable, > 50 ideal
    min_iv_percentile: float = Field(50, ge=0, le=100)
    iv_rank_pct_any: bool = True                           # basta con IV Rank O IV Percentile (no los dos): un pico antiguo de IV no descarta una IV alta de hoy
    min_signals: int = Field(2, ge=0, le=5)                # señales de entrada que deben cumplirse (de las marcadas); 0 = todas las marcadas
    near_level_pct: float = Field(3.0, gt=0, le=50)        # precio a ≤ N % de la media de 50 o de un soporte
    min_option_liquidity: Optional[int] = Field(3, ge=1, le=5)   # None = no exigirla al cargar la estrategia
    price_above_ma200: bool = True
    # False: en temporada de resultados casi todos los vencimientos los incluyen (medido el 2026-10-12: 92 % de los contratos con
    # IV Rank ≥ 30) y el IV Rank alto es en parte la IV de los resultados; evitarlos dejaría el perfil casi vacío. Se puede activar.
    avoid_earnings: bool = False
    # señales de timing: basta con una o dos, por eso el botón solo activa el RSI por defecto
    max_rsi_level: float = Field(35, gt=0, lt=100)         # el RSI debe haber tocado este nivel (o menos) y hoy subir
    max_bollinger_pctb: float = Field(0.30, ge=-1, le=2)   # %B = (precio − banda inferior) / (superior − inferior); ≤ 0,3 = cerca de la inferior
    min_bollinger_width_pct: float = Field(6.0, gt=0, le=100)   # ancho de bandas / media mínimo: evita bandas muy estrechas
    min_iv_hv_ratio: float = Field(1.2, gt=0)              # IV30 / HV30: > 1,2 = las opciones están caras frente a lo que se mueve la acción
    max_iv_change_5d: float = 0.0                          # cambio de la IV en 5 días (puntos) ≤ 0: el miedo deja de crecer
    # VIX: NO es un filtro del scanner, solo el aviso del Panel (`scanner/market.py::vix_advice`)
    min_vix: float = Field(14, ge=0)                       # < 13–14 = primas pobres
    ideal_vix_min: float = Field(18, ge=0)                 # 18–30 y bajando tras un pico = condiciones ideales
    max_vix: float = Field(30, ge=0)                       # por encima, mercado muy nervioso
    min_atr_multiple: float = Field(2.0, gt=0, le=10)      # el strike debe estar al menos a N × ATR(14) del precio (2–3)
    use_atr: bool = True
    expected_move_mult: float = Field(1.0, gt=0, le=5)     # el strike debe quedar por debajo de precio − N × movimiento esperado (precio × IV × √(DTE/365))
    use_expected_move: bool = False                        # el botón no lo activa: 1× equivale a delta ≈ 0,15 y choca con la banda de delta
    use_rsi: bool = True
    use_bollinger: bool = False
    use_bollinger_width: bool = False

    @model_validator(mode="after")
    def _check(self) -> "StrategySettings":
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min no puede superar dte_max")
        if self.min_abs_delta is not None and self.min_abs_delta > self.max_abs_delta:
            raise ValueError("min_abs_delta no puede superar max_abs_delta")
        return self


class ScannerSettings(_Model):
    price_reference: PriceReferenceSettings = PriceReferenceSettings()
    # bid mínimo en $ que SIEMPRE se exige (no es un filtro opcional): con bid 0 nadie compra la put y el ask, a menudo irreal,
    # inflaba el yield con el precio de referencia
    min_bid: float = Field(0.01, gt=0)
    candidates: CandidateRange = CandidateRange()
    # puntos porcentuales que se guardan de más por encima y por debajo de `candidates` (en strikes): así el
    # rango cotizado puede seguir al precio del subyacente sin pedir contratos nuevos a cada oscilación
    catalog_margin_pct: float = Field(5, ge=0, lt=100)
    initial: InitialFilterSettings = InitialFilterSettings()
    filters: FilterSettings = FilterSettings()
    filter_values: FilterValues = FilterValues()
    presets: tuple[ScannerPreset, ...] = DEFAULT_PRESETS
    strategy: StrategySettings = StrategySettings()
    technical: TechnicalSettings = TechnicalSettings()
    quality: QualitySettings = QualitySettings()
    spreads: SpreadSettings = SpreadSettings()

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
    # de dónde sale el sector antes de preguntar a TWS (lento y exige conexión), en este orden: «edgar» (código SIC; necesita `edgar.contact`) y «yahoo»
    # (yfinance, no oficial). Lista vacía = solo IBKR
    sector_sources: list[Literal["edgar", "yahoo"]] = ["edgar", "yahoo"]


class EdgarSettings(_Model):
    """SEC EDGAR (balance y flujos de caja): la SEC exige un contacto en el User-Agent; sin él la función queda apagada."""
    contact: str = ""                                    # tu correo (o nombre y correo): se envía a la SEC en el User-Agent
    refresh_days: int = Field(14, ge=1)                  # cada cuántos días se consulta de nuevo (los informes son trimestrales)
    # horas tras el cierre del último día hábil hasta las que aún pueden llegar presentaciones (EDGAR recibe hasta las 22:00 de Nueva York): las alertas
    # (8-K, NT) no se consultan en fin de semana ni festivo si la última consulta es posterior
    settle_hours: float = Field(6, ge=0)
    requests_per_second: float = Field(5, gt=0, le=10)   # la SEC permite 10 como máximo
    alerts_window_days: int = Field(90, ge=1)            # alertas de 8-K / retrasos de los últimos N días (⚠ junto al ticker)
    alerts_refresh_hours: float = Field(12, gt=0)        # cada cuántas horas se vuelven a consultar las presentaciones
    yahoo_backup: bool = True                            # los tickers que EDGAR no tiene (o con cifras de hace > 15 meses) se completan con Yahoo Finance


class AnalystSettings(_Model):
    """Cambios de valoración de analistas (Yahoo Finance, gratuito y no oficial): ▼/▲ junto al ticker. Solo informa, no filtra."""
    enabled: bool = True
    window_days: int = Field(30, ge=1)                   # cambios de los últimos N días
    refresh_hours: float = Field(20, gt=0)               # cada cuántas horas se vuelven a consultar
    settle_hours: float = Field(0, ge=0)                 # como `edgar.settle_hours`: sin consulta en fin de semana ni festivo si la última es posterior al cierre
    concurrency: int = Field(4, ge=1, le=16)             # peticiones a Yahoo a la vez


class HistorySettings(_Model):
    """Historial de resultados (BPA real frente a estimación, Yahoo) y dividendos (tastytrade) de la ficha de los gráficos. Solo informa."""
    enabled: bool = True
    quarters: int = Field(5, ge=1, le=8)                 # trimestres que se enseñan
    years: int = Field(4, ge=1, le=5)                    # años fiscales de ingresos y beneficio neto que se enseñan (SEC EDGAR; se guardan 5)
    earnings_refresh_hours: float = Field(24, gt=0)      # cada cuántas horas se vuelven a pedir los resultados
    earnings_empty_retry_hours: float = Field(6, gt=0)   # una respuesta SIN resultados (Yahoo limitando, o un ETF) se reintenta antes
    dividends_refresh_hours: float = Field(20, gt=0)     # ... y los dividendos
    settle_hours: float = Field(0, ge=0)                 # como `analysts.settle_hours`: sin consulta en fin de semana ni festivo
    concurrency: int = Field(4, ge=1, le=16)             # peticiones a Yahoo a la vez


class FinnhubSettings(_Model):
    """Finnhub (titulares por empresa). La clave gratuita da 60 peticiones por minuto; sin clave no hay noticias automáticas."""
    api_token: str = Field("", repr=False)


class CompanySettings(_Model):
    """Ficha de cada empresa en el tooltip del ticker: a qué se dedica (Yahoo) y noticias recientes con su fecha (Finnhub, filtradas por palabras
    clave). Solo informa, no filtra. Lo escrito a mano (`python -m scanner_opciones.marketdata.company_cli`) manda sobre lo automático."""
    enabled: bool = True
    description_chars: int = Field(320, ge=60)           # largo máximo de la descripción en el tooltip
    card_chars: int = Field(700, ge=60)                  # y en la ficha de los gráficos de historial y del Panel
    description_refresh_days: int = Field(90, ge=1)      # el negocio casi no cambia
    news_window_days: int = Field(90, ge=1)              # noticias de los últimos N días
    news_max_items: int = Field(4, ge=1, le=10)
    news_refresh_hours: float = Field(24, gt=0)
    settle_hours: float = Field(0, ge=0)                 # como `analysts.settle_hours`: sin consulta en fin de semana ni festivo si la última es posterior al cierre
    calls_per_minute: float = Field(50, gt=0, le=60)     # Finnhub gratuito: 60
    concurrency: int = Field(4, ge=1, le=16)             # peticiones a Yahoo a la vez
    # un titular solo cuenta si menciona alguna de estas palabras (en inglés, sin distinguir mayúsculas) y ninguna de las de `news_exclude`
    # (se busca al comienzo de una palabra: «acquir» vale para acquire/acquires/acquired)
    news_keywords: tuple[str, ...] = (
        "earnings", "guidance", "outlook", "forecast", "revenue", "profit", "loss", "downgrade", "upgrade", "price target", "lawsuit", "sues", "sued",
        "probe", "investigation", "subpoena", "doj", "fda", "recall", "bankruptcy", "default", "offering", "dilution", "buyback",
        "dividend", "acquir", "acquisition", "merger", "takeover", "ceo", "cfo", "resign", "steps down", "layoff", "job cuts", "plunge",
        "tumble", "soar", "short seller", "short report", "delist", "restructur", "going concern", "warning", "approval", "contract",
        "tariff", "banned", "fined", "settlement", "spin-off", "stake")
    news_exclude: tuple[str, ...] = (
        "here's why", "here is why", "should you", "stock to buy", "stocks to buy", "better buy", "buy now", "is it a buy", "right now",
        "prediction", "what to expect", "preview", "reports next week", "what you need to know", "key metrics", "could make you", "millionaire", "motley fool", "zacks", "why is", "what to know", "top stocks")


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


class DefenseSettings(_Model):
    """Defensa de las puts vendidas (aviso en el Panel y evaluación de rolls): cada defensa tiene su momento según lo dentro del dinero que esté la put
    (|delta|), los días que le quedan y la volatilidad implícita (IV Rank). Referencias habituales, no reglas exactas; avisos, nunca órdenes."""
    tested_delta: float = Field(0.30, gt=0, lt=1)      # |delta| desde la que la put está «testeada» (todavía OTM): esperar o vender la call; rolar rinde poco
    atm_delta: float = Field(0.50, gt=0, lt=1)         # ... desde la que está en el dinero o ligeramente dentro: momento de rolar abajo y fuera
    deep_delta: float = Field(0.70, gt=0, lt=1)        # ... desde la que está muy dentro: rolar solo hacia fuera o valorar la asignación
    gamma_dte: int = Field(21, ge=0)                   # a ≤ N DTE el gamma se acelera: gestionar aunque la pérdida no sea extrema
    min_extrinsic: float = Field(0.10, ge=0)           # $/acción; con menos extrínseco en una put dentro del dinero hay riesgo de asignación anticipada
    ex_dividend_days: int = Field(7, ge=0)             # ... y más si el ex-dividendo cae en los próximos N días
    event_days: int = Field(10, ge=0)                  # resultados en los próximos N días (antes del vencimiento): evento binario, valorar protección o cerrar
    loss_multiple: float = Field(2.0, gt=0)            # pérdida de N veces el crédito cobrado: valorar cerrar...
    hard_loss_multiple: float = Field(3.0, gt=0)       # ... y de N veces: cerrar
    ivr_good: float = Field(30, ge=0, le=100)          # IV Rank desde el que rolar paga más (el vencimiento lejano cobra más)
    ivr_high: float = Field(40, ge=0, le=100)          # ... y desde el que es el mejor momento
    roll_dte_min: int = Field(30, ge=1)                # vencimiento del roll: de N a N días...
    roll_dte_max: int = Field(60, ge=1)
    roll_dte_far: int = Field(90, ge=1)                # ... y, si en ese rango no hay crédito, se alarga hasta aquí (más allá se ata mucho tiempo por poco crédito)
    roll_min_credit_pct: float = Field(10, ge=0)       # crédito neto del roll: al menos este % del crédito original (10-20 %)...
    roll_min_credit: float = Field(0.05, ge=0)         # ... y al menos esto por acción (por encima de las comisiones)
    roll_target_delta_min: float = Field(0.30, ge=0, lt=1)   # |delta| deseada tras rolar: volver a 0,30-0,40
    roll_target_delta_max: float = Field(0.40, gt=0, lt=1)
    roll_strikes_below_pct: float = Field(15, gt=0, lt=100)  # strikes candidatos del roll: hasta este % por debajo del precio
    call_delta_min: float = Field(0.20, gt=0, lt=1)    # call de la defensa (mismo vencimiento que la put): |delta| 0,20-0,30
    call_delta_max: float = Field(0.30, gt=0, lt=1)
    protect_strike_pct_min: float = Field(5, gt=0)     # put comprada de protección: strike entre N y N % por debajo...
    protect_strike_pct_max: float = Field(10, gt=0)
    protect_max_debit_pct: float = Field(50, gt=0, le=100)   # ... con un débito que no se coma más de este % del crédito original
    call_min_credit_pct: float = Field(10, ge=0)       # la prima de la call de la defensa: al menos este % del crédito original (si no, no compensa el riesgo)
    call_strikes_above_pct: float = Field(15, gt=0, lt=100)  # strikes candidatos de la call: del precio hasta este % por encima
    freefall_lookback: int = Field(10, ge=3)           # caída libre: sesiones que se miran para medir la caída y el rebote (cierres diarios)
    freefall_drop_pct: float = Field(7, gt=0)          # ... caída mínima (máximo -> mínimo de esa ventana) para hablar de «caída fuerte»
    freefall_days: int = Field(3, ge=1)                # ... caída libre = el mínimo es de las últimas N sesiones y aún no hay rebote
    rebound_pct: float = Field(2, gt=0)                # ... rebote = el último cierre está al menos este % por encima del mínimo
    risk_free_rate: float = Field(0.04, ge=0, lt=1)    # tasa para estimar la delta (Black-Scholes) cuando no hay la real
    # --- spreads de puts (matriz delta de la vendida x DTE x IV Rank, estilo tastylive) ---
    spread_atm_delta: float = Field(0.45, gt=0, lt=1)       # |delta| de la vendida desde la que el precio está en el strike vendido (antes: testeado, desde `tested_delta`)...
    spread_between_delta: float = Field(0.60, gt=0, lt=1)   # ... entre los dos strikes...
    spread_beyond_delta: float = Field(0.80, gt=0, lt=1)    # ... y por debajo de ambos, cerca de la pérdida máxima
    spread_manage_dte: int = Field(30, ge=1)                # con más de N DTE hay tiempo (rolar casi nunca compensa); de `gamma_dte` a N es la zona de gestión
    spread_close_dte: int = Field(7, ge=0)                  # con menos de N DTE se cierra según dónde esté el precio respecto a los strikes
    spread_close_loss_pct: float = Field(50, gt=0, le=100)  # cerrar con una pérdida de este % de la pérdida máxima (o de `loss_multiple` veces el crédito)
    spread_call_min_credit_pct: float = Field(25, ge=0)     # call spread de la defensa: crédito de al menos este % del crédito original (25-30 %)
    spread_roll_dte_min: int = Field(30, ge=1)              # roll del spread: nuevo vencimiento de N a N DTE; si no hay crédito se alarga hasta `far`
    spread_roll_dte_max: int = Field(45, ge=1)
    spread_roll_dte_far: int = Field(60, ge=1)
    greeks_max_age_minutes: float = Field(10, ge=0)    # la delta real de las puts del Panel (cotizadas por DXLink) se reutiliza hasta esta edad con el mercado abierto

    @model_validator(mode="after")
    def _check(self) -> "DefenseSettings":
        if not self.tested_delta < self.atm_delta < self.deep_delta:
            raise ValueError("tested_delta < atm_delta < deep_delta")
        if self.freefall_days > self.freefall_lookback:
            raise ValueError("freefall_days no puede superar freefall_lookback")
        if self.loss_multiple > self.hard_loss_multiple:
            raise ValueError("loss_multiple no puede superar hard_loss_multiple")
        if not self.roll_dte_min <= self.roll_dte_max <= self.roll_dte_far:
            raise ValueError("roll_dte_min <= roll_dte_max <= roll_dte_far")
        if self.roll_target_delta_min > self.roll_target_delta_max or self.call_delta_min > self.call_delta_max:
            raise ValueError("el mínimo de una banda de delta no puede superar su máximo")
        if self.protect_strike_pct_min > self.protect_strike_pct_max:
            raise ValueError("protect_strike_pct_min no puede superar protect_strike_pct_max")
        if not self.tested_delta < self.spread_atm_delta < self.spread_between_delta < self.spread_beyond_delta:
            raise ValueError("tested_delta < spread_atm_delta < spread_between_delta < spread_beyond_delta")
        if not self.spread_roll_dte_min <= self.spread_roll_dte_max <= self.spread_roll_dte_far:
            raise ValueError("spread_roll_dte_min <= spread_roll_dte_max <= spread_roll_dte_far")
        if not self.spread_close_dte < self.gamma_dte < self.spread_manage_dte:
            raise ValueError("spread_close_dte < gamma_dte < spread_manage_dte")
        return self


class ManagementSettings(_Model):
    """Gestión de las puts vendidas (tarjeta del Panel): avisos, no órdenes. Orientativos, no reglas exactas."""
    take_profit_pct: float = Field(50, gt=0, le=100)    # recoger beneficios al capturar este % de la prima cobrada
    take_profit_max_pct: float = Field(65, gt=0, le=100)  # techo del rango de tastytrade (50–65 %): por encima, no esperar más
    review_dte: int = Field(21, ge=0)                   # a este DTE, si no se ha llegado al objetivo: cerrar o rolar
    chart_weeks: int = Field(10, ge=2, le=52)           # semanas de cierres diarios de los gráficos por ticker del Panel
    spread_stop_value_multiple: float = Field(2.0, gt=1)  # spreads (perfil Tastytrade): aviso de stop cuando el spread vale N veces el crédito cobrado (pérdida ≈ 1 × crédito)
    pin_dte: int = Field(5, ge=0)                       # spreads: riesgo de pin / asignación en los últimos N días...
    pin_buffer_pct: float = Field(2.0, ge=0)            # ...si el precio está a menos de este % por encima del strike vendido (o por debajo)
    max_total_risk_pct: float = Field(50, gt=0)         # riesgo total abierto (pérdida máxima de spreads + capital de asignación de puts desnudas) sobre el NLV: aviso por encima
    near_strike_pct: float = Field(2.0, ge=0)           # aviso cuando el precio está a menos de este % por encima del strike
    warn_position_pct: float = Field(5, gt=0)           # peso de una acción (acciones + puts vendidas asignadas) sobre el NLV: aviso
    max_position_pct: float = Field(10, gt=0)           # y por encima de este: alerta
    # Perfil de gestión de cada put: «tastytrade» (lo de arriba: recoger al 50 %, revisar a 21 DTE) o «tpc» (mantener hasta el vencimiento, no
    # rolar). Se elige por posición en el Panel; este es el que se usa mientras no se elija otro.
    default_profile: Literal["tastytrade", "tpc"] = "tpc"
    tpc_close_capture_pct: float = Field(90, gt=0, le=100)   # TPC: aviso (info) de que casi toda la prima está ganada: cerrar o seguir hasta el vencimiento
    tpc_warn_position_pct: float = Field(10, gt=0)      # TPC: peso por acción tras asignación, aviso (techo del método: 15 %)
    tpc_max_position_pct: float = Field(15, gt=0)       # y alerta
    defense: DefenseSettings = DefenseSettings()        # defensa de las puts testeadas / dentro del dinero (perfiles con la defensa activada)

    @model_validator(mode="after")
    def _check(self) -> "ManagementSettings":
        if self.take_profit_max_pct < self.take_profit_pct:
            raise ValueError("take_profit_max_pct no puede ser menor que take_profit_pct")
        if self.warn_position_pct > self.max_position_pct:
            raise ValueError("warn_position_pct no puede superar max_position_pct")
        if self.tpc_warn_position_pct > self.tpc_max_position_pct:
            raise ValueError("tpc_warn_position_pct no puede superar tpc_max_position_pct")
        return self


class CommunitySettings(_Model):
    """Comunidad de traders de Tortuga Trades: un JSON que deja una rutina diaria (`scripts/actualizar_comunidad.ps1`). Los trades
    llegan con un día de retraso (IBKR Flex): sirven para ideas y consenso, no como señal de entrada."""
    path: str = "data/community/tortuga.json"
    source_name: str = "TortugaTrades_Dia"       # fuente del Universo con los tickers más vendidos por la comunidad (se sustituye con cada JSON)
    perm_source_name: str = "TortugaTrades_Perm"  # fuente permanente: tickers que el usuario va añadiendo desde Ideas
    legacy_source_name: str = "Tortuga"          # nombre anterior de la fuente diaria: se renombra al arrancar
    universe_top: int = Field(40, ge=1)    # cuántos tickers entran en esa fuente (los de más traders en 30 días)
    min_traders: int = Field(3, ge=1)      # traders mínimos en 30 días para entrar en la fuente
    stale_hours: int = Field(72, ge=1)     # el Scanner avisa si el JSON es más viejo
    closed_marker_days: int = Field(45, ge=1)   # Ideas: marca las de un ticker en el que cerraste una put vendida hace menos de N días
    openings_path: str = "data/community/aperturas.json"   # fechas exactas de apertura de TUS puts vendidas (`scripts/actualizar_aperturas.ps1`)


class TortugaSettings(_Model):
    """Rutinas que la propia aplicación lanza con la CLI de Claude (`claude -p`, solo lectura) para leer el conector de Tortuga Trades:
    comunidad, aperturas propias y radar de Bull Put Spreads. Sustituyen a las tareas programadas de `scripts/`.
    Cada una se ejecuta una vez al día a su hora (hora local) mientras la aplicación está abierta y solo en días hábiles (fin de semana y festivo esperan al siguiente), y también a mano desde la pestaña Tortuga."""
    enabled: bool = True
    api_token: str = Field("", repr=False)   # token `tt_live_…` de la API de Tortuga (https://tortugatrades.app/api/v1); vacío = sin API
    command: str = "claude"                # ejecutable de la CLI de Claude (debe estar en el PATH y con la sesión iniciada)
    model: str = "sonnet"
    timeout_seconds: int = Field(900, ge=60)  # tiempo máximo de cada rutina
    retry_minutes: int = Field(120, ge=1)     # tras un fallo no se reintenta hasta pasado este tiempo
    prompts_dir: str = "scripts"              # carpeta de los *_prompt.md
    radar_path: str = "data/community/radar_spreads.json"
    source_name: str = "TortugaSpreads"       # fuente del Universo con los tickers del radar (Bull Put Spread) que cumplen el yield del perfil
    # fuente con los del radar cuya estrategia propuesta es la venta de put
    puts_source_name: str = "TortugaPuts"
    profile: str = "TastyPut"                 # perfil del Scanner cuyo yield mínimo (periodo) se exige a la put de 30Δ del radar
    times: dict[str, str] = {"aperturas": "11:10", "comunidad": "11:15", "radar_spreads": "11:45", "super_investors": "11:30"}
    # días mínimos entre dos lecturas buenas de una rutina diaria (los 13F cambian una vez al trimestre: una semana sobra)
    min_days: dict[str, int] = {"super_investors": 7}
    # rutinas que se repiten cada N minutos mientras la aplicación está abierta y el mercado abierto (más una pasada tras el cierre; en vez de una vez al día a su hora de `times`): el radar de
    # Tortuga se refresca durante la sesión (precio e IV cada ~15 min; las primas, en pasadas de ~10 min)
    intervals: dict[str, int] = {"radar_spreads": 30}

    @model_validator(mode="after")
    def _check(self) -> "TortugaSettings":
        for key, minutes in self.intervals.items():
            if minutes < 1:
                raise ValueError(f"tortuga.intervals.{key}: {minutes} minutos no es válido")
        for key, value in self.times.items():
            try:
                hh, mm = value.split(":")
                if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError
            except ValueError:
                raise ValueError(f"tortuga.times.{key}: hora «{value}» no válida (HH:MM)") from None
        return self


class TastyListSource(_Model):
    """Una lista pública de tastytrade que se vuelca en una fuente del Universo."""
    list: str                   # nombre de la lista en tastytrade
    name: str                   # nombre de la fuente en el Universo


class WatchlistSyncSettings(_Model):
    """La Watchlist sigue a las fuentes del Universo. Un ticker que ninguna fuente lista solo se quita pasados `grace_days` días seguidos
    así (las fuentes que se sustituyen enteras, como el radar de Tortuga, sacan y vuelven a meter valores a diario: sin esta gracia se
    borrarían y se volverían a descargar sus contratos). 0 = se quita en cuanto desaparece."""
    grace_days: int = Field(7, ge=0)


class TastyListsSettings(_Model):
    """Listas públicas de tastytrade como fuentes del Universo. Se releen cada `refresh_minutes` (solo en días hábiles, salvo la primera
    vez) y sustituyen a la fuente SOLO si su contenido cambió. No añaden nada a la watchlist: eso se hace desde el Universo, con sus filtros."""
    enabled: bool = True
    refresh_minutes: int = Field(60, ge=5)
    sources: list[TastyListSource] = [
        TastyListSource(list="Dividend Champions", name="DividendChampions"),
        TastyListSource(list="tasty Hourly Top Equities", name="TastyHourly"),
        TastyListSource(list="tasty Fast Movers", name="TastyFastMovers"),
        TastyListSource(list="tasty default", name="TastyDefault"),
        TastyListSource(list="Liquid Symbols", name="TastyLiquid"),
        TastyListSource(list="tasty IVR", name="TastyIVR"),
        TastyListSource(list="Tom's Watchlist", name="TastyTom"),
    ]


class SuperInvestorsSettings(_Model):
    """Fuente del Universo con las mayores posiciones 13F de gestores de calidad y valor (Super Investors de Tortuga). Es una fuente de empresas
    de calidad para puts (pasa por la barra de calidad del Universo y la calidad mínima de las puts)."""
    enabled: bool = True
    path: str = "data/community/super_investors.json"
    source_name: str = "SuperInvestors"
    # slugs de `get_super_investors`: gestores concentrados de calidad y valor (los cuantitativos, activistas y de crecimiento especulativo no)
    managers: list[str] = ["buffett", "fundsmith", "akre", "sequoia", "yacktman", "pabrai", "himalaya", "russo", "dodgecox", "harris",
                           "firsteagle", "tweedy", "weitz", "brave", "southeastern"]
    min_weight_pct: float = Field(2.0, ge=0)   # peso mínimo en la cartera 13F del gestor para contar como posición de convicción
    min_managers: int = Field(1, ge=1)         # nº mínimo de gestores que la tienen con ese peso


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
    analysts: AnalystSettings = AnalystSettings()
    history: HistorySettings = HistorySettings()
    finnhub: FinnhubSettings = FinnhubSettings()
    company: CompanySettings = CompanySettings()
    trend: TrendSettings = TrendSettings()
    storage: StorageSettings = StorageSettings()
    logging: LoggingSettings = LoggingSettings()
    market: MarketSettings = MarketSettings()
    management: ManagementSettings = ManagementSettings()
    community: CommunitySettings = CommunitySettings()
    tortuga: TortugaSettings = TortugaSettings()
    tasty_lists: TastyListsSettings = TastyListsSettings()
    watchlist_sync: WatchlistSyncSettings = WatchlistSyncSettings()
    super_investors: SuperInvestorsSettings = SuperInvestorsSettings()
    instruments: dict[str, InstrumentSettings] = Field(default_factory=default_instruments)   # índices y otros subyacentes de TWS (ticker de la app -> ajustes)

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
