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

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import Position
from scanner_opciones.portfolio import defense as defense_mod
from scanner_opciones.portfolio.defense import Defense, DefenseInput
from scanner_opciones.portfolio.diversification import nominal as nominal_of
from scanner_opciones.portfolio.exits import ExitRules, defaults as style_rules
from scanner_opciones.portfolio.spreads import with_spreads


TASTYTRADE, TPC = "tastytrade", "tpc"
PROFILES = (TASTYTRADE, TPC)


def put_key(p: Position) -> str:
    """Identifica una put vendida para recordar su perfil de gestión (en un spread, la pata vendida)."""
    o = p.option
    return f"{p.ticker}|{o.expiry.isoformat()}|{o.strike:g}"


# (patrón del comienzo del aviso, etiqueta corta): la primera que coincide manda. El texto completo se ve en el tooltip.
_SHORT_LABELS = (
    (r"Recoger beneficios", "Recoger beneficios"), (r"Casi toda la prima", "Prima casi ganada"), (r"Revisar:", "Revisar / rolar"),
    (r"En pérdida", "En pérdida"), (r"Put testeada", "Testeada: esperar"), (r"En el dinero \(", "Rolar abajo y fuera"),
    (r"Muy dentro", "Muy dentro: rolar fuera"), (r"Quedan \d+ días", "Gamma: gestionar ya"),
    (r"Extrínseco casi cero", "Asignación anticipada"), (r"Ex-dividendo", "Ex-dividendo cerca"), (r"Pérdida de", "Pérdida alta"),
    (r"IV Rank", "IV Rank"), (r"Resultados", "Resultados antes del venc."), (r"Stop:", "Stop: valorar cerrar"),
    (r"Riesgo de pin", "Riesgo de pin"), (r"Pérdida máxima:", "Pérdida máxima"), (r"En el dinero:", "En el dinero"),
    (r"El precio", "Cerca del strike"), (r"Cierra:", "Cerrar"), (r"Valora cerrar", "Valorar cerrar"),
    (r"Con tanto tiempo", "Sin prisa: no rolar"), (r"Si la tesis", "Revisar la tesis"),
    (r"Cayó .*NO vendas", "Caída libre: no vender call"), (r"Cayó .*buen momento", "Rebote: vender call"),
    (r"Cayó ", "Frenado: esperar rebote"), (r"Roll ya:", "Roll ya: ↻"), (r"Roll no aún:", "Roll: aún no"), (r"Roll sin prisa:", "Roll: sin prisa"),
)
# Avisos de un spread (empiezan por «Δ 0,30: …»): se resumen por la acción que aconsejan.
_SPREAD_ACTIONS = (
    ("cierra siempre", "Cerrar (pin)"), ("cierra o deja que se ejerzan", "Cerrar o ejercer"), ("deja expirar", "Dejar expirar"),
    ("añade un call spread", "Añadir call spread"), ("rola fuera YA", "Rolar ya con crédito"), ("rola abajo y fuera", "Rolar abajo y fuera"),
    ("rola", "Rolar con crédito"), ("espera a", "Esperar a 21 DTE"), ("espera", "Esperar"), ("déjalo expirar", "Dejar expirar"),
    ("cierra o déjalo", "Cerrar o dejar correr"), ("deja correr", "Dejar correr o cerrar"),
)
_ICONS = {"warn": "⚠", "ok": "✔", "info": "ⓘ"}
_URGENCY = {"warn": 3, "ok": 2, "info": 1}


@dataclass(frozen=True)
class Alert:
    level: str    # ok | info | warn
    text: str

    @property
    def icon(self) -> str:
        return _ICONS.get(self.level, "ⓘ")

    @property
    def short(self) -> str:
        """Resumen de pocas palabras para la tabla del Panel; el texto completo queda para el tooltip."""
        if self.text.startswith("Δ ") or self.text.startswith("sin delta"):
            tail = self.text.split(": ", 1)[-1]
            for key, label in _SPREAD_ACTIONS:
                if key in tail:
                    return label
            return "Gestionar"
        for pattern, label in _SHORT_LABELS:
            m = re.match(pattern, self.text)
            if m:
                if label == "IV Rank":
                    n = re.match(r"IV Rank (\d+)", self.text)
                    return f"IV Rank {n.group(1)} %" if n else label
                return label
        return self.text.split(":")[0][:28]


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
    assigned: str = ""                   # perfil del Scanner al que pertenece la put (por DTE de apertura o elegido a mano)
    opened: Optional[date] = None        # fecha de apertura (de Tortuga si es exacta, si no la primera vez que la app vio la posición)
    opened_exact: bool = False
    margin: Optional[float] = None       # margen comprometido en $ (todos los contratos): what-if guardado (put desnuda) o pérdida máxima (spread)
    margin_exact: bool = False           # False = estimado con el what-if de una put nueva, no con el margen real de IBKR
    assign_note: str = ""                # cómo se reconoció el perfil de la posición (por los filtros al abrir o a mano)
    rules: Optional[ExitRules] = None    # estrategia de salida aplicada (la del perfil; None = la del estilo `profile`)
    defense: Optional[Defense] = None    # zona de la put (testeada / en el dinero / muy dentro) y defensa aconsejada; None si el perfil no la usa o es un spread

    @property
    def main_alert(self) -> Optional[Alert]:
        """El aviso más urgente (warn > ok > info; a igualdad, el primero): es el que se ve en la tabla."""
        return min(self.alerts, key=lambda a: -_URGENCY.get(a.level, 0), default=None)    # min conserva el primero en los empates

    @property
    def urgency(self) -> int:
        """3 = actuar (warn), 2 = cerrar con ganancia (ok), 1 = informativo, 0 = sin avisos."""
        a = self.main_alert
        return _URGENCY.get(a.level, 0) if a else 0


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
    margin_total: float = 0.0           # suma del margen conocido de las puts vendidas
    margin_missing: int = 0             # puts sin margen conocido (no entran en la suma)
    margin_pct: Optional[float] = None  # ... sobre el NLV


def _short_puts(positions: Sequence[Position]) -> list[Position]:
    return [p for p in positions if p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0]


def committed_margin(p: Position, entry: Optional[float], contracts: int, what_if: Optional[float]) -> Optional[float]:
    """Margen que inmoviliza la posición (todos sus contratos). Spread: su pérdida máxima ((ancho − crédito) × multiplicador; sin crédito
    conocido, el ancho entero). Put desnuda: el margen what-if guardado de ese contrato (por contrato), o None si no se calculó."""
    o = p.option
    if p.hedge_strike is not None:
        net = max(o.strike - p.hedge_strike - (entry or 0.0), 0.0)
        return net * o.multiplier * contracts
    return what_if * contracts if what_if is not None else None


def put_status(p: Position, today: date, underlying: Optional[float], cfg: ManagementSettings, profile: str = TASTYTRADE,
               what_if_margin: Optional[float] = None, rules: Optional[ExitRules] = None, defense_ctx: Optional[dict] = None,
               live_delta: Optional[float] = None) -> PutStatus:
    """`rules`: estrategia de salida del perfil de la put; sin ella, la del estilo `profile` (tastytrade | tpc) con los valores de `cfg`.
    `defense_ctx`: datos del subyacente para la defensa (`iv_rank`, `iv30` en fracción, `next_earnings`, `ex_dividend_in`).
    `live_delta`: |delta| real de la put (cotización de tastytrade); sin ella la defensa la estima con Black-Scholes."""
    o = p.option
    contracts = int(abs(p.quantity))
    dte = (o.expiry - today).days
    per_share = contracts * o.multiplier
    current = abs(p.market_value) / per_share if per_share else None
    entry = abs(p.avg_cost) / o.multiplier if p.avg_cost else None
    captured = (entry - current) / entry * 100 if entry and current is not None else None
    r = rules or style_rules(profile, cfg)
    alerts: list[Alert] = []
    if r.take_profit_pct is not None and captured is not None and captured >= r.take_profit_pct:
        rango = f"{r.take_profit_pct:g}–{r.take_profit_max_pct:g} %" if r.take_profit_max_pct else f"{r.take_profit_pct:g} %"
        extra = " Ya supera el rango: no esperes más." if r.take_profit_max_pct and captured > r.take_profit_max_pct else ""
        alerts.append(Alert("ok", f"Recoger beneficios: {captured:.0f} % de la prima ganada (objetivo {rango}).{extra}"))
    elif r.close_capture_pct is not None and captured is not None and captured >= r.close_capture_pct:
        alerts.append(Alert("info", f"Casi toda la prima ganada ({captured:.0f} %): puedes cerrarla y liberar la obligación o mantenerla hasta el vencimiento."))
    elif r.review_dte is not None and 0 <= dte <= r.review_dte:
        got = "sin dato de la prima" if captured is None else f"{captured:.0f} % de la prima ganada"
        goal = f" y no se ha llegado al {r.take_profit_pct:g} %" if r.take_profit_pct is not None else ""
        alerts.append(Alert("warn", f"Revisar: quedan {dte} días (≤ {r.review_dte}){goal} "
                                    f"({got}). Cerrarla o rolarla a un vencimiento posterior cobrando crédito."))
    if captured is not None and captured < 0:
        alerts.append(Alert("info", "En pérdida: recomprarla cuesta más que la prima cobrada."))
    spread = p.hedge_strike is not None
    defense = None
    if r.defense:
        c = defense_ctx or {}
        if not spread:
            defense = defense_mod.assess(DefenseInput(o.expiry, o.strike, dte, underlying, entry, current, c.get("iv_rank"), c.get("iv30"),
                                                      c.get("next_earnings"), c.get("ex_dividend_in"), today, live_delta, c.get("closes")), cfg.defense)
        else:      # matriz del spread: delta de la put VENDIDA x DTE x IV Rank
            defense = defense_mod.assess_spread(defense_mod.SpreadInput(
                o.expiry, o.strike, p.hedge_strike, dte, underlying, entry, current, live_delta, c.get("iv_rank"), c.get("iv30"),
                c.get("next_earnings"), today, c.get("closes")), cfg.defense)
        if defense is not None:
            alerts.extend(Alert(level, text) for level, text in defense.steps)
    if spread and r.stop_multiple is not None and entry and current is not None and current >= r.stop_multiple * entry:
        alerts.append(Alert("warn", f"Stop: el spread vale {current / entry:.1f} veces el crédito cobrado (límite {r.stop_multiple:g}×): "
                                    "la pérdida ya iguala o supera el crédito. Valora cerrarlo."))
    if spread and underlying is not None and 0 <= dte <= r.pin_dte and underlying <= o.strike * (1 + r.pin_buffer_pct / 100):
        alerts.append(Alert("warn", f"Riesgo de pin: quedan {dte} días y el precio ({underlying:.2f}) está a ≤ {r.pin_buffer_pct:g} % del strike vendido "
                                    f"({o.strike:g}). Cerrarlo antes del vencimiento evita una asignación parcial."))
    if underlying is not None:
        if p.hedge_strike is not None and underlying <= p.hedge_strike:
            alerts.append(Alert("warn", f"Pérdida máxima: el precio ({underlying:.2f}) está por debajo de la put comprada ({p.hedge_strike:g}); "
                                        f"el spread ya vale su ancho ({o.strike - p.hedge_strike:g})."))
        elif underlying <= o.strike and defense is not None and (spread or defense.zone in (defense_mod.ATM, defense_mod.DEEP)):
            pass     # la defensa ya dice qué hacer con una put (o un spread) en el dinero
        elif underlying <= o.strike:
            todo = ("Mantener sin rolar: no se rola; si te asignan, reevalúa la tesis y valora vender calls." if r.itm_action == "hold"
                    else "Valora cerrar antes que esperar la asignación.")
            alerts.append(Alert("warn", f"En el dinero: el precio ({underlying:.2f}) está por debajo del strike. {todo}"))
        elif (underlying - o.strike) / underlying * 100 < cfg.near_strike_pct:
            alerts.append(Alert("info", f"El precio ({underlying:g}) está a menos de {cfg.near_strike_pct:g} % del strike."))
    return PutStatus(p.ticker, o.expiry, o.strike, contracts, dte, entry, current, captured, underlying, tuple(alerts), p.hedge_strike,
                     profile, put_key(p), margin=committed_margin(p, entry, contracts, what_if_margin), margin_exact=spread, rules=r, defense=defense)


def ticker_weights(positions: Sequence[Position], net_liquidation: Optional[float], cfg: ManagementSettings,
                   profiles: Optional[dict[str, str]] = None, rules: Optional[dict[str, ExitRules]] = None) -> list[TickerWeight]:
    """Peso sobre el NLV de cada acción con puts vendidas: acciones que ya tienes + lo que comprarías si te asignan todo.
    Los umbrales son los de la estrategia de salida de cada put; si la acción tiene varias, los más estrictos (los holgados de TPC solo
    valen si TODAS las puts de la acción se gestionan con ellos)."""
    puts = _short_puts(positions)
    profiles, rules = profiles or {}, rules or {}
    out = []
    for ticker in sorted({p.ticker for p in puts}):
        stock = sum(p.market_value * p.fx for p in positions if p.ticker == ticker and p.option is None and p.quantity > 0)
        mine = [p for p in puts if p.ticker == ticker]
        nominal = sum(nominal_of(p) for p in mine)       # en un spread, su ancho: la put comprada limita lo que compras
        total = stock + nominal
        pct = total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 else None
        mine_rules = [rules.get(put_key(p)) or style_rules(profiles.get(put_key(p), cfg.default_profile), cfg) for p in mine]
        warn, top = min(r.warn_position_pct for r in mine_rules), min(r.max_position_pct for r in mine_rules)
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
            risk = max(risk - abs(p.avg_cost) * abs(p.quantity) * p.fx, 0.0)
        total += risk
    return total


def manage_puts(
    positions: Sequence[Position], net_liquidation: Optional[float], today: date,
    prices: dict[str, Optional[float]], cfg: ManagementSettings, profiles: Optional[dict[str, str]] = None,
    assigned: Optional[dict[str, str]] = None, opened: Optional[dict[str, tuple[date, bool]]] = None,
    margins: Optional[dict[str, float]] = None, notes: Optional[dict[str, str]] = None, rules: Optional[dict[str, ExitRules]] = None,
    defense_ctx: Optional[dict[str, dict]] = None, live_deltas: Optional[dict[str, float]] = None,
) -> ManagementReport:
    """`profiles`: perfil de gestión elegido por put (`put_key`); las que no están usan `cfg.default_profile`.
    `rules`: estrategia de salida de cada put (`put_key`, la de su perfil); las que no están usan la del estilo.
    `margins`: margen what-if guardado por contrato de cada put desnuda (`put_key`); en un spread el margen es su pérdida máxima.
    `defense_ctx`: por ticker, los datos para la defensa (ver `put_status`). `live_deltas`: |delta| real de cada put (`put_key`)."""
    positions = with_spreads(positions)     # cada spread es una sola fila: prima y valor netos, riesgo = ancho
    profiles = profiles or {}
    assigned, opened, margins, notes, rules = assigned or {}, opened or {}, margins or {}, notes or {}, rules or {}
    puts = sorted((replace(put_status(p, today, prices.get(p.ticker), cfg, profiles.get(put_key(p), cfg.default_profile), margins.get(put_key(p)), rules.get(put_key(p)), (defense_ctx or {}).get(p.ticker), (live_deltas or {}).get(put_key(p))),
                           assigned=assigned.get(put_key(p), ""), assign_note=notes.get(put_key(p), ""), opened=opened.get(put_key(p), (None, False))[0],
                           opened_exact=opened.get(put_key(p), (None, False))[1])
                   for p in _short_puts(positions)), key=lambda s: (s.expiry, s.ticker, s.strike))
    weights = ticker_weights(positions, net_liquidation, cfg, profiles, rules)
    total = open_risk(positions)
    risk_pct = total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 and total > 0 else None
    risk_level = "warn" if risk_pct is not None and risk_pct > cfg.max_total_risk_pct else None
    attention = (sum(1 for s in puts for a in s.alerts if a.level == "warn") + sum(1 for w in weights if w.level == "warn")
                 + (1 if risk_level else 0))
    margin_total = sum(s.margin for s in puts if s.margin is not None)
    margin_pct = margin_total / net_liquidation * 100 if net_liquidation and net_liquidation > 0 and margin_total > 0 else None
    return ManagementReport(tuple(puts), tuple(weights), attention, total, risk_pct, risk_level,
                            margin_total, sum(1 for s in puts if s.margin is None), margin_pct)
