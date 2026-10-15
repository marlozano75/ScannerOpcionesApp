"""Gestión de las puts vendidas: cuándo recoger beneficios, cuándo revisar y cuánto pesa cada acción si te asignan.

Solo avisos sobre las posiciones abiertas (la aplicación no envía órdenes). Funciones puras. Cada put se gestiona con un perfil
(`TASTYTRADE` por defecto, `TPC` si se elige): reglas orientativas.
  · Tastytrade: recoger beneficios al capturar el 50–65 % de la prima; a 21 DTE, si no se ha llegado al 50 %, cerrar o rolar a un
    vencimiento posterior cobrando crédito; una asignación no debería superar un 5–10 % de la cartera por acción.
  · TPC (The Premium Collector): mantener hasta el vencimiento, sin recoger al 50 % ni rolar (si te asignan, reevaluar la tesis y
    valorar vender calls); solo se avisa de que casi toda la prima está ganada, y el peso por acción puede llegar al 15 %.
La prima capturada necesita el coste medio de la posición (`Position.avg_cost`); sin él solo se avisa por DTE y precio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import Position
from scanner_opciones.portfolio.diversification import nominal as nominal_of
from scanner_opciones.portfolio.spreads import with_spreads


TASTYTRADE, TPC = "tastytrade", "tpc"
PROFILES = (TASTYTRADE, TPC)


def put_key(p: Position) -> str:
    """Identifica una put vendida para recordar su perfil de gestión (en un spread, la pata vendida)."""
    o = p.option
    return f"{p.ticker}|{o.expiry.isoformat()}|{o.strike:g}"


@dataclass(frozen=True)
class Alert:
    level: str    # ok | info | warn
    text: str


@dataclass(frozen=True)
class PutStatus:
    ticker: str
    expiry: date
    strike: float
    contracts: int
    dte: int
    entry_credit: Optional[float]       # prima cobrada por acción (de avg_cost)
    current_price: Optional[float]      # lo que cuesta recomprarla ahora, por acción
    captured_pct: Optional[float]       # % de la prima ya ganado (negativo = en pérdida)
    underlying_price: Optional[float]
    alerts: tuple[Alert, ...]
    long_strike: Optional[float] = None  # put comprada si forma parte de un spread (prima y valor ya son netos)
    profile: str = TASTYTRADE            # perfil de gestión aplicado
    key: str = ""                        # `put_key`, para cambiar el perfil desde el Panel


@dataclass(frozen=True)
class TickerWeight:
    ticker: str
    stock_value: float
    put_nominal: float
    total: float
    pct_nlv: Optional[float]
    level: Optional[str]                # None | info | warn
    contracts: int


@dataclass(frozen=True)
class ManagementReport:
    puts: tuple[PutStatus, ...]         # por vencimiento
    weights: tuple[TickerWeight, ...]   # de mayor a menor peso
    attention: int                      # alertas "warn" en total
    total_risk: float = 0.0             # pérdida máxima de los spreads + capital de asignación de las puts desnudas, en USD
    risk_pct: Optional[float] = None    # ... sobre el NLV
    risk_level: Optional[str] = None    # None | warn (supera `max_total_risk_pct`)


def _short_puts(positions: Sequence[Position]) -> list[Position]:
    return [p for p in positions if p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0]


def put_status(p: Position, today: date, underlying: Optional[float], cfg: ManagementSettings, profile: str = TASTYTRADE) -> PutStatus:
    o = p.option
    contracts = int(abs(p.quantity))
    dte = (o.expiry - today).days
    per_share = contracts * o.multiplier
    current = abs(p.market_value) / per_share if per_share else None
    entry = abs(p.avg_cost) / o.multiplier if p.avg_cost else None
    captured = (entry - current) / entry * 100 if entry and current is not None else None
    alerts: list[Alert] = []
    tpc = profile == TPC
    if tpc:
        if captured is not None and captured >= cfg.tpc_close_capture_pct:
            alerts.append(Alert("info", f"Casi toda la prima ganada ({captured:.0f} %): puedes cerrarla y liberar la obligación o mantenerla hasta el vencimiento."))
    elif captured is not None and captured >= cfg.take_profit_pct:
        rango = f"{cfg.take_profit_pct:g}–{cfg.take_profit_max_pct:g} %"
        extra = " Ya supera el rango: no esperes más." if captured > cfg.take_profit_max_pct else ""
        alerts.append(Alert("ok", f"Recoger beneficios: {captured:.0f} % de la prima ganada (objetivo {rango}).{extra}"))
    elif 0 <= dte <= cfg.review_dte:
        got = "sin dato de la prima" if captured is None else f"{captured:.0f} % de la prima ganada"
        alerts.append(Alert("warn", f"Revisar: quedan {dte} días (≤ {cfg.review_dte}) y no se ha llegado al {cfg.take_profit_pct:g} % "
                                    f"({got}). Cerrarla o rolarla a un vencimiento posterior cobrando crédito."))
    if captured is not None and captured < 0:
        alerts.append(Alert("info", "En pérdida: recomprarla cuesta más que la prima cobrada."))
    spread = p.hedge_strike is not None
    if spread and not tpc and entry and current is not None and current >= cfg.spread_stop_value_multiple * entry:
        alerts.append(Alert("warn", f"Stop: el spread vale {current / entry:.1f} veces el crédito cobrado (límite {cfg.spread_stop_value_multiple:g}×): "
                                    "la pérdida ya iguala o supera el crédito. Valora cerrarlo."))
    if spread and underlying is not None and 0 <= dte <= cfg.pin_dte and underlying <= o.strike * (1 + cfg.pin_buffer_pct / 100):
        alerts.append(Alert("warn", f"Riesgo de pin: quedan {dte} días y el precio ({underlying:.2f}) está a ≤ {cfg.pin_buffer_pct:g} % del strike vendido "
                                    f"({o.strike:g}). Cerrarlo antes del vencimiento evita una asignación parcial."))
    if underlying is not None:
        if p.hedge_strike is not None and underlying <= p.hedge_strike:
            alerts.append(Alert("warn", f"Pérdida máxima: el precio ({underlying:.2f}) está por debajo de la put comprada ({p.hedge_strike:g}); "
                                        f"el spread ya vale su ancho ({o.strike - p.hedge_strike:g})."))
        elif underlying <= o.strike:
            todo = ("Método TPC: no se rola; si te asignan, reevalúa la tesis y valora vender calls." if tpc
                    else "Valora cerrar antes que esperar la asignación.")
            alerts.append(Alert("warn", f"En el dinero: el precio ({underlying:.2f}) está por debajo del strike. {todo}"))
        elif (underlying - o.strike) / underlying * 100 < cfg.near_strike_pct:
            alerts.append(Alert("info", f"El precio ({underlying:g}) está a menos de {cfg.near_strike_pct:g} % del strike."))
    return PutStatus(p.ticker, o.expiry, o.strike, contracts, dte, entry, current, captured, underlying, tuple(alerts), p.hedge_strike,
                     profile, put_key(p))


def ticker_weights(positions: Sequence[Position], net_liquidation: Optional[float], cfg: ManagementSettings,
                   profiles: Optional[dict[str, str]] = None) -> list[TickerWeight]:
    """Peso sobre el NLV de cada acción con puts vendidas: acciones que ya tienes + lo que comprarías si te asignan todo.
    Los umbrales son los de TPC (más holgados) solo si TODAS las puts de la acción se gestionan con TPC."""
    puts = _short_puts(positions)
    profiles = profiles or {}
    out = []
    for ticker in sorted({p.ticker for p in puts}):
        stock = sum(p.market_value for p in positions if p.ticker == ticker and p.option is None and p.quantity > 0)
        mine = [p for p in puts if p.ticker == ticker]
        nominal = sum(nominal_of(p) for p in mine)       # en un spread, su ancho: la put comprada limita lo que compras
        total = stock + nominal
        pct = total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 else None
        tpc = all(profiles.get(put_key(p), cfg.default_profile) == TPC for p in mine)
        warn, top = (cfg.tpc_warn_position_pct, cfg.tpc_max_position_pct) if tpc else (cfg.warn_position_pct, cfg.max_position_pct)
        level = None if pct is None else ("warn" if pct > top else "info" if pct > warn else None)
        out.append(TickerWeight(ticker, stock, nominal, total, pct, level, int(sum(abs(p.quantity) for p in mine))))
    return sorted(out, key=lambda w: -(w.pct_nlv if w.pct_nlv is not None else -1))


def open_risk(positions: Sequence[Position]) -> float:
    """Lo máximo que pueden perder las puts vendidas abiertas: en un spread, su ancho menos el crédito cobrado (sin crédito conocido, el ancho
    entero); en una put desnuda, su capital de asignación (strike × multiplicador), sin descontar la prima. `positions` ya con los spreads fundidos."""
    total = 0.0
    for p in _short_puts(positions):
        risk = nominal_of(p)
        if p.hedge_strike is not None and p.avg_cost:
            risk = max(risk - abs(p.avg_cost) * abs(p.quantity), 0.0)
        total += risk
    return total


def manage_puts(
    positions: Sequence[Position], net_liquidation: Optional[float], today: date,
    prices: dict[str, Optional[float]], cfg: ManagementSettings, profiles: Optional[dict[str, str]] = None,
) -> ManagementReport:
    """`profiles`: perfil de gestión elegido por put (`put_key`); las que no están usan `cfg.default_profile`."""
    positions = with_spreads(positions)     # cada spread es una sola fila: prima y valor netos, riesgo = ancho
    profiles = profiles or {}
    puts = sorted((put_status(p, today, prices.get(p.ticker), cfg, profiles.get(put_key(p), cfg.default_profile))
                   for p in _short_puts(positions)), key=lambda s: (s.expiry, s.ticker, s.strike))
    weights = ticker_weights(positions, net_liquidation, cfg, profiles)
    total = open_risk(positions)
    risk_pct = total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 and total > 0 else None
    risk_level = "warn" if risk_pct is not None and risk_pct > cfg.max_total_risk_pct else None
    attention = (sum(1 for s in puts for a in s.alerts if a.level == "warn") + sum(1 for w in weights if w.level == "warn")
                 + (1 if risk_level else 0))
    return ManagementReport(tuple(puts), tuple(weights), attention, total, risk_pct, risk_level)
