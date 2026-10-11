"""Gestión de las puts vendidas: cuándo recoger beneficios, cuándo revisar y cuánto pesa cada acción si te asignan.

Solo avisos sobre las posiciones abiertas (la aplicación no envía órdenes). Funciones puras. Reglas orientativas:
  · recoger beneficios al capturar el 50 % de la prima;
  · a 21 DTE, si no se ha llegado al 50 %, cerrar o rolar a un vencimiento posterior cobrando crédito;
  · una asignación no debería superar un 5–10 % de la cartera por acción.
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


def _short_puts(positions: Sequence[Position]) -> list[Position]:
    return [p for p in positions if p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0]


def put_status(p: Position, today: date, underlying: Optional[float], cfg: ManagementSettings) -> PutStatus:
    o = p.option
    contracts = int(abs(p.quantity))
    dte = (o.expiry - today).days
    per_share = contracts * o.multiplier
    current = abs(p.market_value) / per_share if per_share else None
    entry = abs(p.avg_cost) / o.multiplier if p.avg_cost else None
    captured = (entry - current) / entry * 100 if entry and current is not None else None
    alerts: list[Alert] = []
    if captured is not None and captured >= cfg.take_profit_pct:
        alerts.append(Alert("ok", f"Recoger beneficios: {captured:.0f} % de la prima ganada (objetivo {cfg.take_profit_pct:g} %)."))
    elif 0 <= dte <= cfg.review_dte:
        got = "sin dato de la prima" if captured is None else f"{captured:.0f} % de la prima ganada"
        alerts.append(Alert("warn", f"Revisar: quedan {dte} días (≤ {cfg.review_dte}) y no se ha llegado al {cfg.take_profit_pct:g} % "
                                    f"({got}). Cerrarla o rolarla a un vencimiento posterior cobrando crédito."))
    if captured is not None and captured < 0:
        alerts.append(Alert("info", "En pérdida: recomprarla cuesta más que la prima cobrada."))
    if underlying is not None:
        if p.hedge_strike is not None and underlying <= p.hedge_strike:
            alerts.append(Alert("warn", f"Pérdida máxima: el precio ({underlying:g}) está por debajo de la put comprada ({p.hedge_strike:g}); "
                                        f"el spread ya vale su ancho ({o.strike - p.hedge_strike:g})."))
        elif underlying <= o.strike:
            alerts.append(Alert("warn", f"En el dinero: el precio ({underlying:g}) está por debajo del strike. Valora cerrar antes que esperar la asignación."))
        elif (underlying - o.strike) / underlying * 100 < cfg.near_strike_pct:
            alerts.append(Alert("info", f"El precio ({underlying:g}) está a menos de {cfg.near_strike_pct:g} % del strike."))
    return PutStatus(p.ticker, o.expiry, o.strike, contracts, dte, entry, current, captured, underlying, tuple(alerts), p.hedge_strike)


def ticker_weights(positions: Sequence[Position], net_liquidation: Optional[float], cfg: ManagementSettings) -> list[TickerWeight]:
    """Peso sobre el NLV de cada acción con puts vendidas: acciones que ya tienes + lo que comprarías si te asignan todo."""
    puts = _short_puts(positions)
    out = []
    for ticker in sorted({p.ticker for p in puts}):
        stock = sum(p.market_value for p in positions if p.ticker == ticker and p.option is None and p.quantity > 0)
        mine = [p for p in puts if p.ticker == ticker]
        nominal = sum(nominal_of(p) for p in mine)       # en un spread, su ancho: la put comprada limita lo que compras
        total = stock + nominal
        pct = total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 else None
        level = None if pct is None else ("warn" if pct > cfg.max_position_pct else "info" if pct > cfg.warn_position_pct else None)
        out.append(TickerWeight(ticker, stock, nominal, total, pct, level, int(sum(abs(p.quantity) for p in mine))))
    return sorted(out, key=lambda w: -(w.pct_nlv if w.pct_nlv is not None else -1))


def manage_puts(
    positions: Sequence[Position], net_liquidation: Optional[float], today: date,
    prices: dict[str, Optional[float]], cfg: ManagementSettings,
) -> ManagementReport:
    positions = with_spreads(positions)     # cada spread es una sola fila: prima y valor netos, riesgo = ancho
    puts = sorted((put_status(p, today, prices.get(p.ticker), cfg) for p in _short_puts(positions)), key=lambda s: (s.expiry, s.ticker, s.strike))
    weights = ticker_weights(positions, net_liquidation, cfg)
    attention = sum(1 for s in puts for a in s.alerts if a.level == "warn") + sum(1 for w in weights if w.level == "warn")
    return ManagementReport(tuple(puts), tuple(weights), attention)
