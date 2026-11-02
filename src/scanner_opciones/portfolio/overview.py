"""Estado de la cartera y detalle de las posiciones que no son puts vendidas (perfil «Otros»).

Funciones puras sobre `Position`/`AccountSummary`. Las puts vendidas y los spreads se detallan en `management.py`; aquí van las acciones,
las calls (vendidas o compradas) y las puts compradas sueltas. Ninguna tiene perfil de gestión: se les asigna `OTHERS`.
La prima capturada solo existe en las opciones VENDIDAS (la parte de lo cobrado que ya es beneficio); acciones y opciones compradas no tienen.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, Position
from scanner_opciones.portfolio.spreads import Combo, split_combos, split_spreads

OTHERS = "Otros"


def leg_key(o) -> str:
    """Identifica una pata de opción para buscar su delta: `TICKER|vencimiento|strike|C/P` (ticker con el símbolo de IBKR)."""
    return f"{o.ticker}|{o.expiry.isoformat()}|{o.strike:g}|{'C' if o.right is OptionRight.CALL else 'P'}"


def other_key(op: "OtherPosition") -> str:
    """Clave estable de una «otra posición» o combo para recordar su perfil de gestión (mismo formato `TICKER|venc|strike` que `put_key`)."""
    exp = op.expiry.isoformat() if op.expiry else ""
    sid = op.strikes if op.strikes else (f"{op.strike:g}" if op.strike is not None else "")
    return f"{op.ticker}|{exp}|{sid}"


@dataclass(frozen=True)
class OtherPosition:
    ticker: str
    kind: str                           # Acción | Call vendida | Call comprada | Put comprada
    quantity: float
    expiry: Optional[date]
    strike: Optional[float]
    dte: Optional[int]
    entry: Optional[float]              # precio medio de entrada por acción (opción: prima por acción)
    current: Optional[float]            # precio actual por acción
    value: float                        # valor de mercado en USD (con signo)
    pnl: Optional[float]                # ganancia/pérdida latente en USD
    pnl_pct: Optional[float]            # ... sobre lo invertido o cobrado
    premium: Optional[float]            # prima cobrada en USD (solo opciones vendidas)
    captured: Optional[float]           # prima capturada en USD (cobrada − coste de recompra)
    captured_pct: Optional[float]
    pct_nlv: Optional[float]            # |valor| sobre el NLV
    profile: str = OTHERS
    underlying: Optional[float] = None  # precio actual del subyacente
    strikes: Optional[str] = None       # combos: strikes de las patas ("100/105"); si los vencimientos difieren, también ellos
    key: str = ""                       # `other_key`, para cambiar el perfil desde el Panel
    levels: tuple[float, ...] = ()      # strikes numéricos de las patas (para el gráfico; vacío en una acción)
    legs: tuple[tuple[float, bool, str, date], ...] = ()   # (strike, vendida, "Call"/"Put", vencimiento) de cada pata: colorea el gráfico y busca su delta
    max_risk: Optional[float] = None    # USD que se pueden perder como mucho: lo pagado (compras, debit) o ancho − crédito (credit spread vertical); None si no está acotado
    width: Optional[float] = None       # combo vertical: ancho entre strikes
    # --- gestión (`portfolio/other_mgmt.py`) ---
    alerts: tuple = ()                  # `management.Alert`
    zone: str = ""                      # clave de zona (ver `other_mgmt`); "" = sin zona
    zone_label: str = ""
    delta: Optional[float] = None       # |delta| de la pata de referencia
    delta_live: bool = False            # True = real de tastytrade, False = estimada
    rules: object = None                # `exits.ExitRules` aplicadas
    take_profit_pct: Optional[float] = None   # objetivo de recogida (en % de `captured_pct`) para pintar el capturado en verde
    captured_note: str = ""
    opened: Optional[date] = None       # fecha de apertura (de Tortuga si es exacta, si no la primera vez que la app vio la posición: IBKR no la da)
    opened_exact: bool = False

    @property
    def is_short_option(self) -> bool:
        return self.premium is not None

    @property
    def main_alert(self):
        """El aviso más urgente (warn > ok > info; a igualdad, el primero), como en las puts."""
        order = {"warn": 3, "ok": 2, "info": 1}
        return min(self.alerts, key=lambda a: -order.get(a.level, 0), default=None)

    @property
    def urgency(self) -> int:
        a = self.main_alert
        return {"warn": 3, "ok": 2, "info": 1}.get(a.level, 0) if a else 0


@dataclass(frozen=True)
class PortfolioStatus:
    nlv: Optional[float]                # en la moneda base de la cuenta
    currency: str
    daily_pnl: Optional[float]
    daily_pct: Optional[float]          # sobre el NLV de ayer (NLV − cambio del día)
    unrealized_pnl: Optional[float]
    positions: int
    stock_value: float                  # USD
    stock_pnl: Optional[float]          # latente de las acciones, USD
    premium: float                      # prima cobrada por TODAS las opciones vendidas, USD
    captured: float                     # ... y la parte ya capturada, USD
    captured_pct: Optional[float]


def _other_kind(p: Position) -> str:
    if p.option is None:
        return "Acción"
    right = "Call" if p.option.right is OptionRight.CALL else "Put"
    return f"{right} {'vendida' if p.quantity < 0 else 'comprada'}"


def _combo_position(c: Combo, today: date, nlv_usd: Optional[float], underlying: Optional[float] = None) -> OtherPosition:
    """Fila de un combo: precios netos por acción (débito o crédito, en positivo); sus patas se resumen en `strikes`."""
    legs, n = c.legs, c.contracts
    mult, fx = legs[0].option.multiplier, legs[0].fx
    sign = lambda p: 1 if p.quantity > 0 else -1
    value = sum(p.market_value for p in legs) * fx                       # >0 débito, <0 crédito
    current = abs(sum(p.market_value for p in legs)) / (n * mult)
    entry = cost = None
    if all(p.avg_cost is not None for p in legs):
        net = sum(sign(p) * abs(p.avg_cost) for p in legs)               # por contrato; >0 débito, <0 crédito
        entry, cost = abs(net) / mult, net * n * fx
    pnl = value - cost if cost is not None else None
    pct = pnl / abs(cost) * 100 if pnl is not None and cost else None
    credit = cost is not None and cost < 0
    expiry = min(p.option.expiry for p in legs)
    strikes = "/".join(f"{p.option.strike:g}" for p in sorted(legs, key=lambda p: p.option.strike))
    if len({p.option.expiry for p in legs}) > 1:
        strikes += " · " + " / ".join(p.option.expiry.strftime("%d/%m") for p in sorted(legs, key=lambda p: p.option.strike))
    vertical = len(legs) == 2 and legs[0].option.right is legs[1].option.right and legs[0].option.expiry == legs[1].option.expiry
    width = abs(legs[0].option.strike - legs[1].option.strike) if vertical else None
    if cost is None:
        max_risk = None
    elif not credit:
        max_risk = cost                                      # débito pagado
    else:
        max_risk = width * mult * n * fx + cost if width is not None else None    # cost < 0: ancho − crédito
    return OtherPosition(
        legs[0].ticker, c.name, n, expiry, None, (expiry - today).days, entry, current, value, pnl, pct,
        -cost if credit else None, pnl if credit else None, pct if credit else None,
        abs(value) / nlv_usd * 100 if nlv_usd and nlv_usd > 0 else None, underlying=underlying, strikes=strikes,
        levels=tuple(sorted(p.option.strike for p in legs)),
        legs=tuple(sorted((p.option.strike, p.quantity < 0, "Call" if p.option.right is OptionRight.CALL else "Put", p.option.expiry) for p in legs)),
        max_risk=max_risk, width=width)


def other_positions(positions: Sequence[Position], today: date, nlv_usd: Optional[float],
                    prices: Optional[dict[str, Optional[float]]] = None,
                    assigned: Optional[dict[str, str]] = None, default_profile: str = OTHERS,
                    opened: Optional[dict[str, tuple[date, bool]]] = None) -> list[OtherPosition]:
    """Todo lo que no es una put vendida (ni parte de un spread): acciones, calls y puts compradas sueltas y combos. Más peso primero.
    `assigned`: perfil elegido a mano por `other_key`; el resto va a `default_profile` (el catch-all de gestión). `opened`: (fecha de apertura, exacta) por `other_key`."""
    assigned, opened = assigned or {}, opened or {}
    out = []
    rest, _ = split_spreads(positions)
    rest, combos = split_combos(rest)
    out.extend(_combo_position(c, today, nlv_usd, (prices or {}).get(c.legs[0].ticker)) for c in combos)
    for p in rest:
        if p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0:
            continue                                  # put vendida o spread: tabla de gestión
        if p.quantity == 0:
            continue
        o, qty, fx = p.option, abs(p.quantity), p.fx
        mult = o.multiplier if o else 1
        entry = abs(p.avg_cost) / mult if p.avg_cost else None
        current = abs(p.market_value) / (qty * mult) if qty else None
        value = p.market_value * fx
        short_option = o is not None and p.quantity < 0
        cost = entry * qty * mult * fx if entry is not None else None           # lo pagado (largo) o cobrado (corto), en USD
        pnl = pct = premium = captured = cpct = None
        if cost is not None:
            pnl = (cost - abs(value)) if short_option else (abs(value) - cost)
            pct = pnl / cost * 100 if cost else None
            if short_option:
                premium, captured = cost, pnl
                cpct = pct
        out.append(OtherPosition(
            p.ticker, _other_kind(p), p.quantity, o.expiry if o else None, o.strike if o else None,
            (o.expiry - today).days if o else None, entry, current, value, pnl, pct, premium, captured, cpct,
            abs(value) / nlv_usd * 100 if nlv_usd and nlv_usd > 0 else None, underlying=(prices or {}).get(p.ticker),
            levels=(o.strike,) if o else (),
            legs=((o.strike, p.quantity < 0, "Call" if o.right is OptionRight.CALL else "Put", o.expiry),) if o else (),
            max_risk=cost if o is not None and not short_option else None))
    out = [replace(op, key=other_key(op), profile=assigned.get(other_key(op), default_profile), opened=opened.get(other_key(op), (None, False))[0], opened_exact=opened.get(other_key(op), (None, False))[1]) for op in out]
    return sorted(out, key=lambda x: -abs(x.value))


def portfolio_status(account: Optional[AccountSummary], others: Sequence[OtherPosition], puts: Sequence) -> PortfolioStatus:
    """Resumen de la cartera. `puts`: los `PutStatus` de las puts y spreads vendidos; `others`: `other_positions`."""
    nlv = account.net_liquidation if account else None
    daily = account.daily_pnl if account else None
    base = nlv - daily if nlv is not None and daily is not None else None
    stocks = [x for x in others if x.kind == "Acción"]
    premium = sum(s.premium_usd or 0.0 for s in puts) + sum(x.premium or 0.0 for x in others)
    captured = sum(s.captured_usd or 0.0 for s in puts) + sum(x.captured or 0.0 for x in others)
    return PortfolioStatus(
        nlv, (account.base_currency if account and account.base_currency else "USD"), daily,
        daily / base * 100 if base else None, account.unrealized_pnl if account else None,
        len(puts) + len(others), sum(x.value for x in stocks), sum(x.pnl for x in stocks if x.pnl is not None) if stocks else None,
        premium, captured, captured / premium * 100 if premium else None)
