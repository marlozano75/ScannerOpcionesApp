"""Gestión de las posiciones que NO son puts vendidas (calls vendidas, opciones compradas, combos y acciones): zona por delta, avisos y objetivo de
recogida, con la misma forma que las puts (`management.Alert`, chips de zona, urgencia de la fila, «Capturado» en verde al llegar al objetivo).

Cuatro familias, cada una con las reglas de salida (`exits.ExitRules`) del perfil de la posición:
  · Crédito (call vendida, credit spread, straddle/strangle vendido): recoger al `take_profit_pct` de la prima, revisar a `review_dte`, stop a
    `stop_multiple` × el crédito, pin, en el dinero. La zona es la de las puts vendidas (delta de la pata vendida más expuesta).
  · Opción comprada y combos de débito sin ancho (calendar, diagonal, straddle comprado): recoger a `long_take_profit_pct` de lo pagado, stop a
    `long_stop_pct`, revisar a `long_review_dte`. La zona va al revés: más delta = más dentro del dinero = mejor.
  · Debit spread vertical: lo mismo, pero el objetivo es `debit_take_profit_pct` del beneficio máximo (ancho − débito).
  · Acción: sin avisos (el peso sobre el NLV se muestra, pero no hay umbral).
La delta es la real de tastytrade (`deltas`, por `overview.leg_key`, con signo) y, sin ella, la estimada con Black-Scholes (call = 1 − |delta| de la put
del mismo strike). Solo avisos: la aplicación no envía órdenes. Funciones puras."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Mapping, Optional

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.metrics.greeks import estimate_abs_delta
from scanner_opciones.portfolio import defense as defense_mod
from scanner_opciones.portfolio.exits import ExitRules, defaults as style_rules
from scanner_opciones.portfolio.management import Alert
from scanner_opciones.portfolio.overview import OtherPosition

# Zonas de una posición comprada (a más delta, más dentro del dinero y mejor para quien la tiene)
LONG_FAR, LONG_NEAR, LONG_ITM, LONG_DEEP = "lfar", "lnear", "litm", "ldeep"
LONG_LABELS = {LONG_FAR: "lejos del dinero: necesita un movimiento grande", LONG_NEAR: "cerca del dinero",
               LONG_ITM: "en el dinero (a favor)", LONG_DEEP: "muy dentro del dinero (a favor)"}
LONG_SHORT = {LONG_FAR: "Lejos", LONG_NEAR: "Cerca", LONG_ITM: "En el dinero", LONG_DEEP: "Muy dentro"}

CREDIT, LONG, DEBIT, STOCK = "credit", "long", "debit", "stock"


def family(op: OtherPosition) -> str:
    if not op.legs and op.strike is None:
        return STOCK
    if op.premium is not None:
        return CREDIT
    if len(op.legs) == 1:
        return LONG
    return DEBIT


def _leg_delta(op: OtherPosition, leg: tuple, deltas: Mapping[str, float], iv30: Optional[float], today: date,
               cfg: ManagementSettings) -> tuple[Optional[float], bool]:
    """(|delta|, es_real) de una pata; la estimada con Black-Scholes si no hay cotización."""
    strike, _, right, expiry = leg
    real = deltas.get(f"{op.ticker}|{expiry.isoformat()}|{strike:g}|{right[0]}")
    if real is not None:
        return abs(real), True
    if not op.underlying:
        return None, False
    est = estimate_abs_delta(op.underlying, strike, (expiry - today).days, None, iv30, cfg.defense.risk_free_rate)
    if est is None:
        return None, False
    return (1 - est if right == "Call" else est), False


def _long_zone(delta: Optional[float], op: OtherPosition, strike: float, right: str, cfg: ManagementSettings) -> str:
    if delta is None:
        if not op.underlying:
            return LONG_NEAR
        itm = op.underlying > strike if right == "Call" else op.underlying < strike
        return LONG_ITM if itm else LONG_FAR
    d = cfg.defense
    return LONG_DEEP if delta >= d.deep_delta else LONG_ITM if delta >= d.atm_delta else LONG_NEAR if delta >= d.tested_delta else LONG_FAR


def _short_zone(delta: Optional[float], op: OtherPosition, strike: float, right: str, cfg: ManagementSettings) -> str:
    if delta is None:
        if not op.underlying:
            return defense_mod.CALM
        itm = op.underlying > strike if right == "Call" else op.underlying < strike
        return defense_mod.ATM if itm else defense_mod.CALM
    return defense_mod.zone_of(delta, op.underlying, strike, cfg.defense)


def _beyond(price: Optional[float], strike: float, right: str) -> bool:
    return price is not None and (price >= strike if right == "Call" else price <= strike)


def assess(op: OtherPosition, rules: Optional[ExitRules], cfg: ManagementSettings, today: date, deltas: Optional[Mapping[str, float]] = None,
           iv30: Optional[float] = None) -> OtherPosition:
    """`op` con zona, delta, avisos, objetivo y «Capturado» rellenos según la estrategia de salida `rules` (sin ella, el estilo Tastytrade)."""
    r = rules or style_rules("tastytrade", cfg)
    fam = family(op)
    if fam == STOCK:
        return replace(op, rules=r, alerts=())
    deltas = deltas or {}
    legs = list(op.legs)
    side = [l for l in legs if l[1]] if fam == CREDIT else [l for l in legs if not l[1]]
    side = side or legs
    scored = [(_leg_delta(op, l, deltas, iv30, today, cfg), l) for l in side]
    (delta, live), ref = max(scored, key=lambda t: (t[0][0] is not None, t[0][0] or 0.0))
    strike, _, right, _ = ref
    if fam == CREDIT:
        zone = _short_zone(delta, op, strike, right, cfg)
        label = defense_mod.ZONE_LABELS[zone]
    else:
        zone = _long_zone(delta, op, strike, right, cfg)
        label = LONG_LABELS[zone]
    captured, target, note = op.captured_pct, None, "Parte de la prima ya ganada"
    if fam == CREDIT:
        target = r.take_profit_pct
        alerts = _credit_alerts(op, r, cfg, today, zone, delta)
    elif fam == LONG or op.width is None:
        captured, target, note = op.pnl_pct, r.long_take_profit_pct, "Ganancia sobre lo pagado"
        alerts = _long_alerts(op, r, today, captured)
    else:
        captured, target, note = _debit_capture(op), r.debit_take_profit_pct, "Beneficio máximo ya capturado"
        alerts = _long_alerts(op, r, today, captured, debit_spread=True)
    return replace(op, rules=r, zone=zone, zone_label=label, delta=delta, delta_live=live, alerts=tuple(alerts),
                   captured_pct=captured if fam != CREDIT else op.captured_pct, take_profit_pct=target, captured_note=note)


def _debit_capture(op: OtherPosition) -> Optional[float]:
    """% del beneficio máximo de un debit spread vertical ya capturado: (valor − débito) / (ancho − débito)."""
    if op.width is None or op.entry is None or op.current is None or op.width <= op.entry:
        return None
    return (op.current - op.entry) / (op.width - op.entry) * 100


def _credit_alerts(op: OtherPosition, r: ExitRules, cfg: ManagementSettings, today: date, zone: str, delta: Optional[float]) -> list[Alert]:
    out: list[Alert] = []
    cap, dte, price = op.captured_pct, op.dte if op.dte is not None else 9999, op.underlying
    if r.take_profit_pct is not None and cap is not None and cap >= r.take_profit_pct:
        rango = f"{r.take_profit_pct:g}–{r.take_profit_max_pct:g} %" if r.take_profit_max_pct else f"{r.take_profit_pct:g} %"
        out.append(Alert("ok", f"Recoger beneficios: {cap:.0f} % de la prima ganada (objetivo {rango})."))
    elif r.close_capture_pct is not None and cap is not None and cap >= r.close_capture_pct:
        out.append(Alert("info", f"Casi toda la prima ganada ({cap:.0f} %): puedes cerrarla o mantenerla hasta el vencimiento."))
    elif r.review_dte is not None and 0 <= dte <= r.review_dte:
        goal = f" y no se ha llegado al {r.take_profit_pct:g} %" if r.take_profit_pct is not None else ""
        got = "sin dato de la prima" if cap is None else f"{cap:.0f} % de la prima ganada"
        out.append(Alert("warn", f"Revisar: quedan {dte} días (≤ {r.review_dte}){goal} ({got}). Cerrarla o rolarla a un vencimiento posterior cobrando crédito."))
    if cap is not None and cap < 0:
        out.append(Alert("info", "En pérdida: recomprarla cuesta más que la prima cobrada."))
    shorts = [l for l in op.legs if l[1]]
    longs = [l for l in op.legs if not l[1]]
    if r.stop_multiple is not None and op.entry and op.current is not None and op.current >= r.stop_multiple * op.entry:
        out.append(Alert("warn", f"Stop: vale {op.current / op.entry:.1f} veces el crédito cobrado (límite {r.stop_multiple:g}×): la pérdida ya iguala o supera el crédito. Valora cerrar."))
    if price is not None and 0 <= dte <= r.pin_dte and any(abs(price - k) / k * 100 <= r.pin_buffer_pct for k, _, _, _ in shorts):
        out.append(Alert("warn", f"Riesgo de pin: quedan {dte} días y el precio ({price:.2f}) está a ≤ {r.pin_buffer_pct:g} % de un strike vendido. Cerrarlo antes del vencimiento evita una asignación parcial."))
    if price is not None and longs and shorts and op.width is not None and all(_beyond(price, k, rt) for k, _, rt, _ in longs):
        out.append(Alert("warn", f"Pérdida máxima: el precio ({price:.2f}) ya pasó la pata comprada; el spread vale su ancho ({op.width:g})."))
    elif any(_beyond(price, k, rt) for k, _, rt, _ in shorts):
        todo = ("Mantener sin rolar: no se rola; si te asignan, reevalúa la tesis." if r.itm_action == "hold"
                else "Valora cerrar o rolar antes que esperar la asignación.")
        out.append(Alert("warn", f"En el dinero: el precio ({price:.2f}) ha pasado un strike vendido. {todo}"))
    elif zone == defense_mod.TESTED:
        out.append(Alert("info", f"Testeada: Δ {delta:.2f}, el precio se acerca al strike vendido. Esperar; rolar rinde poco todavía."))
    elif price is not None and shorts and min(abs(price - k) / price * 100 for k, _, _, _ in shorts) < cfg.near_strike_pct:
        out.append(Alert("info", f"El precio ({price:g}) está a menos de {cfg.near_strike_pct:g} % de un strike vendido."))
    return out


def _long_alerts(op: OtherPosition, r: ExitRules, today: date, gain: Optional[float], debit_spread: bool = False) -> list[Alert]:
    out: list[Alert] = []
    dte = op.dte if op.dte is not None else 9999
    target = r.debit_take_profit_pct if debit_spread else r.long_take_profit_pct
    pnl_pct = op.pnl_pct
    if target is not None and gain is not None and gain >= target:
        base = "del beneficio máximo" if debit_spread else "sobre lo pagado"
        out.append(Alert("ok", f"Recoger beneficios: {gain:.0f} % {base} (objetivo {target:g} %)."))
    elif r.long_review_dte is not None and 0 <= dte <= r.long_review_dte:
        out.append(Alert("warn", f"Revisar: quedan {dte} días (≤ {r.long_review_dte}) y no se ha llegado al objetivo: el valor temporal se evapora. Cerrarla o rolarla."))
    if r.long_stop_pct is not None and pnl_pct is not None and pnl_pct <= -r.long_stop_pct:
        out.append(Alert("warn", f"Stop: pierde un {-pnl_pct:.0f} % de lo pagado (límite {r.long_stop_pct:g} %). Valora cerrar."))
    elif pnl_pct is not None and pnl_pct < 0:
        out.append(Alert("info", f"En pérdida: vale un {-pnl_pct:.0f} % menos de lo pagado."))
    return out
