"""Defensa de las puts vendidas: qué hacer cuando la put se ve amenazada. Cada defensa tiene su momento según tres variables: lo dentro del dinero
que está la put (|delta|), los días que le quedan y la volatilidad implícita (IV Rank).

  · Testeada pero todavía OTM (|delta| 0,30-0,50): esperar o vender la call; rolar aún rinde poco.
  · En el dinero o ligeramente dentro (0,50-0,70): el momento de rolar abajo y fuera, porque aún hay extrínseco que capturar.
  · Muy dentro (≥ 0,70): rolar solo hacia fuera o valorar la asignación (poco extrínseco, poco crédito).
  · ≤ 21 DTE: gestionar aunque la pérdida no sea extrema (el gamma se acelera). Extrínseco casi nulo en una put dentro del dinero: rolar ya
    (asignación anticipada, sobre todo antes de un dividendo).
  · Rolar: crédito neto siempre (si no hay al mismo strike, alargar el plazo; si tampoco, cerrar o aceptar la asignación), 30-60 DTE, strike lo más
    bajo posible con un crédito mínimo (10-20 % del original), |delta| de vuelta a 0,30-0,40, mejor con IV Rank alto y sin resultados en el nuevo vencimiento.

Solo avisos y comparativas: la aplicación no envía órdenes. Referencias habituales, no reglas fijas: todos los umbrales están en `management.defense`.
Funciones puras (`assess` para el Panel, `rank_rolls` para la evaluación bajo demanda con cotizaciones en vivo)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import DefenseSettings
from scanner_opciones.metrics.greeks import estimate_abs_delta

CALM, TESTED, ATM, DEEP = "calm", "tested", "atm", "deep"
BETWEEN, BEYOND = "between", "beyond"          # spreads: entre los dos strikes / por debajo de ambos
ZONE_LABELS = {CALM: "tranquila", TESTED: "testeada", ATM: "en el dinero", DEEP: "muy dentro del dinero",
               BETWEEN: "entre los dos strikes", BEYOND: "por debajo de ambos strikes"}


@dataclass(frozen=True)
class DefenseInput:
    expiry: date
    strike: float
    dte: int
    underlying: Optional[float]
    entry: Optional[float]                 # crédito cobrado por acción
    current: Optional[float]               # coste de recomprarla ahora, por acción
    iv_rank: Optional[float] = None        # %
    iv30: Optional[float] = None           # fracción (0,28 = 28 %); respaldo de la IV si el precio de la put no la determina
    next_earnings: Optional[date] = None
    ex_dividend_in: Optional[int] = None   # días hasta el próximo ex-dividendo
    today: Optional[date] = None
    delta: Optional[float] = None          # |delta| real (cotización en vivo); si no, se estima con Black-Scholes


@dataclass(frozen=True)
class Defense:
    zone: str
    delta: Optional[float]                 # |delta|: la real (cotización de tastytrade) si se pasó, si no la estimada (Black-Scholes con la IV que implica el precio)
    extrinsic: Optional[float]             # valor extrínseco por acción (precio actual − intrínseco)
    steps: tuple[tuple[str, str], ...]     # (nivel `info`|`warn`, texto)
    delta_live: bool = False               # True si `delta` es la real del proveedor

    @property
    def label(self) -> str:
        return ZONE_LABELS[self.zone]


def zone_of(delta: Optional[float], underlying: Optional[float], strike: float, cfg: DefenseSettings) -> str:
    """Zona de la put por su |delta|; sin delta (faltan datos para estimarla), por si el precio está por debajo del strike."""
    if delta is None:
        return CALM if underlying is None or underlying >= strike else ATM
    if delta >= cfg.deep_delta:
        return DEEP
    if delta >= cfg.atm_delta:
        return ATM
    if delta >= cfg.tested_delta:
        return TESTED
    return CALM


def assess(i: DefenseInput, cfg: DefenseSettings) -> Optional[Defense]:
    """Defensa aconsejada para una put desnuda. None sin precio del subyacente (no se puede situar la put)."""
    if i.underlying is None or i.underlying <= 0:
        return None
    delta = i.delta if i.delta is not None else estimate_abs_delta(i.underlying, i.strike, i.dte, i.current, i.iv30, cfg.risk_free_rate)
    zone = zone_of(delta, i.underlying, i.strike, cfg)
    intrinsic = max(i.strike - i.underlying, 0.0)
    extrinsic = max(i.current - intrinsic, 0.0) if i.current is not None else None
    steps: list[tuple[str, str]] = []
    d = f"Δ {delta:.2f}" if delta is not None else "sin delta"
    ext = f"{extrinsic:.2f} $ de extrínseco" if extrinsic is not None else "extrínseco desconocido"
    itm = i.underlying < i.strike
    good_iv = i.iv_rank is not None and i.iv_rank >= cfg.ivr_good

    # 1. según lo dentro del dinero que esté
    if zone == TESTED:
        steps.append(("info", f"Put testeada ({d}, aún fuera del dinero): espera o vende la call (mismo vencimiento, Δ {cfg.call_delta_min:g}–{cfg.call_delta_max:g}, "
                              "mejor tras una caída fuerte y algo de rebote, no en caída libre). Rolar ahora suele rendir poco."))
    elif zone == ATM:
        steps.append(("warn", f"En el dinero ({d}, {ext}): es el momento de rolar abajo y fuera, aún hay valor extrínseco que capturar. "
                              f"Busca crédito neto, {cfg.roll_dte_min}–{cfg.roll_dte_max} DTE y volver a Δ {cfg.roll_target_delta_min:g}–{cfg.roll_target_delta_max:g}."))
    elif zone == DEEP:
        steps.append(("warn", f"Muy dentro del dinero ({d}, {ext}): rola solo hacia fuera (mismo strike o casi) o valora la asignación; "
                              "con tan poco extrínseco los rolls dan poco crédito."))
    # 2. tiempo
    if zone != CALM and 0 <= i.dte <= cfg.gamma_dte:
        steps.append(("warn", f"Quedan {i.dte} días (≤ {cfg.gamma_dte}): gestiónala ya aunque la pérdida no sea extrema; a partir de aquí el gamma se acelera."))
    # 3. asignación anticipada
    if itm and extrinsic is not None and extrinsic <= cfg.min_extrinsic:
        why = ""
        if i.ex_dividend_in is not None and 0 <= i.ex_dividend_in <= cfg.ex_dividend_days:
            why = f" y el ex-dividendo es en {i.ex_dividend_in} días"
        steps.append(("warn", f"Extrínseco casi cero ({extrinsic:.2f} $) con la put dentro del dinero{why}: riesgo de asignación anticipada. Rola ya o prepárate para la asignación."))
    elif itm and i.ex_dividend_in is not None and 0 <= i.ex_dividend_in <= cfg.ex_dividend_days:
        steps.append(("info", f"Ex-dividendo en {i.ex_dividend_in} días con la put dentro del dinero: puede ejercerse antes del vencimiento."))
    # 4. pérdida
    if i.entry and i.current is not None and i.entry > 0:
        loss = (i.current - i.entry) / i.entry
        if loss >= cfg.hard_loss_multiple:
            steps.append(("warn", f"Pérdida de {loss:.1f} veces el crédito cobrado (límite {cfg.hard_loss_multiple:g}×): cerrar o, si te gusta el valor, aceptar la asignación."))
        elif loss >= cfg.loss_multiple:
            steps.append(("warn", f"Pérdida de {loss:.1f} veces el crédito cobrado (aviso a {cfg.loss_multiple:g}×): valora cerrar si la tesis se ha roto."))
    # 5. volatilidad y eventos (solo cuando hay que decidir algo)
    if zone in (ATM, DEEP, TESTED) and i.iv_rank is not None:
        if i.iv_rank >= cfg.ivr_high:
            steps.append(("info", f"IV Rank {i.iv_rank:.0f} %: el mejor momento para rolar, el vencimiento lejano paga más."))
        elif good_iv:
            steps.append(("info", f"IV Rank {i.iv_rank:.0f} %: rolar paga razonablemente."))
        elif zone != TESTED:
            steps.append(("info", f"IV Rank {i.iv_rank:.0f} % (< {cfg.ivr_good:g}): rolar pagará poco; si no hay crédito, cerrar o aceptar la asignación."))
    if zone != CALM and i.next_earnings is not None and i.today is not None and i.today <= i.next_earnings <= i.expiry:
        days = (i.next_earnings - i.today).days
        if days <= cfg.event_days:
            steps.append(("warn", f"Resultados en {days} días ({i.next_earnings:%d/%m}) antes del vencimiento: evento binario. Valora comprar una put de protección "
                                  f"({cfg.protect_strike_pct_min:g}–{cfg.protect_strike_pct_max:g} % por debajo, débito de como mucho el {cfg.protect_max_debit_pct:g} % del crédito) o cerrar."))
        else:
            steps.append(("info", f"Resultados el {i.next_earnings:%d/%m} antes del vencimiento: al rolar, evita meterlos en el nuevo vencimiento salvo que lo hagas a propósito."))
    return Defense(zone, delta, extrinsic, tuple(steps), i.delta is not None)


# ---- evaluación de rolls con cotizaciones en vivo ----------------------------------------------------------------------------
@dataclass(frozen=True)
class RollQuote:
    """Cotización de una put candidata a destino del roll."""
    expiry: date
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    delta: Optional[float] = None    # |delta| (la del proveedor)
    long_strike: Optional[float] = None   # roll de un spread: strike de la put comprada del nuevo spread (bid/ask son los del spread completo)


@dataclass(frozen=True)
class RollOption:
    expiry: date
    dte: int
    strike: float
    bid: float
    delta: Optional[float]
    net_credit: float                # por acción: lo que cobra la nueva (bid) menos lo que cuesta cerrar la actual (ask)
    credit_pct: Optional[float]      # ... en % del crédito original
    ok: bool                         # cumple el crédito mínimo (neto y % del original)
    notes: tuple[str, ...]
    long_strike: Optional[float] = None   # roll de un spread: put comprada del nuevo spread


@dataclass(frozen=True)
class RollReport:
    best: tuple[RollOption, ...]     # el mejor strike de cada vencimiento, por plazo
    verdict: str                     # `roll` | `extend` | `none`
    text: str


def rank_rolls(strike: float, entry: Optional[float], close_cost: float, quotes: Sequence[RollQuote], today: date, cfg: DefenseSettings,
               dte_range: Optional[tuple[int, int, int]] = None, none_text: Optional[str] = None) -> RollReport:
    """Mejor roll de la put: del strike actual hacia abajo y con vencimiento posterior, SIEMPRE con crédito neto. `close_cost`: lo que cuesta recomprar la
    put actual (su ask). Por cada vencimiento se elige el strike más bajo que da el crédito mínimo (el que más se aleja), con preferencia por
    una |delta| final de 0,30–0,40. Si en 30–60 DTE no hay crédito, se alarga hasta `roll_dte_far`; si tampoco, la defensa no es viable.
    `dte_range` = (mínimo, máximo, lejano) sustituye al de `cfg` (los spreads rolan a 30–45 y alargan a 60); `none_text`, el veredicto sin crédito.
    En un spread cada `RollQuote` trae el crédito del NUEVO spread completo en `bid` y `close_cost` es lo que cuesta cerrar el actual."""
    dmin, dmax, dfar = dte_range or (cfg.roll_dte_min, cfg.roll_dte_max, cfg.roll_dte_far)
    need_pct = entry * cfg.roll_min_credit_pct / 100 if entry else 0.0
    need = max(cfg.roll_min_credit, need_pct)
    options: dict[date, list[RollOption]] = {}
    for q in quotes:
        dte = (q.expiry - today).days
        if q.bid is None or q.bid <= 0 or q.strike > strike + 1e-9 or dte < dmin or dte > dfar:
            continue
        net = q.bid - close_cost
        notes = []
        if dte > dmax:
            notes.append(f"{dte} DTE: más allá de {dmax} se ata mucho tiempo por poco crédito extra")
        if q.delta is not None and q.delta > cfg.atm_delta:
            notes.append(f"Δ {q.delta:.2f} sigue por encima de {cfg.atm_delta:g}: solo compras tiempo, no probabilidad")
        elif q.delta is not None and q.delta > cfg.roll_target_delta_max:
            notes.append(f"Δ {q.delta:.2f} por encima del objetivo ({cfg.roll_target_delta_min:g}–{cfg.roll_target_delta_max:g})")
        pct = net / entry * 100 if entry else None
        options.setdefault(q.expiry, []).append(RollOption(q.expiry, dte, q.strike, q.bid, q.delta, net, pct, net >= need - 1e-9, tuple(notes), q.long_strike))
    best: list[RollOption] = []
    for expiry in sorted(options):
        rows = options[expiry]
        good = [o for o in rows if o.ok]
        pool = [o for o in good if o.delta is None or o.delta <= cfg.roll_target_delta_max] or good
        if pool:
            best.append(min(pool, key=lambda o: o.strike))                 # el strike más bajo que mantiene el crédito mínimo
        else:
            best.append(max((o for o in rows if o.net_credit > 0), key=lambda o: o.net_credit, default=max(rows, key=lambda o: o.net_credit)))
    in_range = [o for o in best if o.ok and o.dte <= dmax]
    extended = [o for o in best if o.ok and o.dte > dmax]
    if in_range:
        top = min(in_range, key=lambda o: (o.strike, o.dte))
        return RollReport(tuple(best), "roll", f"Roll viable: {top.expiry:%d/%m/%Y} ({top.dte} DTE) al strike {top.strike:g} cobra {top.net_credit:.2f} $/acción neto.")
    if extended:
        top = min(extended, key=lambda o: o.dte)
        return RollReport(tuple(best), "extend", f"Solo hay crédito alargando el plazo: {top.expiry:%d/%m/%Y} ({top.dte} DTE) al strike {top.strike:g} "
                                                 f"cobra {top.net_credit:.2f} $/acción neto.")
    return RollReport(tuple(best), "none", none_text or "No hay roll con crédito neto suficiente ni alargando el plazo: la defensa no es viable. Cierra la put o acepta la asignación "
                                                     "(si te gusta el valor a ese precio y la posición cabe en la cartera).")


# ---- spreads de puts: matriz delta de la vendida x DTE x IV Rank -------------------------------------------------------------
@dataclass(frozen=True)
class SpreadInput:
    expiry: date
    short_strike: float
    long_strike: float
    dte: int
    underlying: Optional[float]
    entry: Optional[float]                 # crédito neto cobrado por acción
    current: Optional[float]               # lo que cuesta cerrar el spread ahora, por acción
    delta: Optional[float] = None          # |delta| de la put VENDIDA (cotización en vivo); si no, se estima con la IV30 del subyacente
    iv_rank: Optional[float] = None
    iv30: Optional[float] = None
    next_earnings: Optional[date] = None
    today: Optional[date] = None

    @property
    def width(self) -> float:
        return self.short_strike - self.long_strike


def spread_zone(delta: Optional[float], underlying: Optional[float], short_strike: float, long_strike: float, cfg: DefenseSettings) -> str:
    """Dónde está el precio según la |delta| de la put vendida: cómodo (< 0,30), testeado, en el strike vendido (0,45-0,60), entre los dos strikes
    (0,60-0,80) o por debajo de ambos (> 0,80). Sin delta, por el precio frente a los strikes."""
    if delta is None:
        if underlying is None or underlying >= short_strike:
            return CALM
        return BEYOND if underlying <= long_strike else BETWEEN
    if delta >= cfg.spread_beyond_delta:
        return BEYOND
    if delta >= cfg.spread_between_delta:
        return BETWEEN
    if delta >= cfg.spread_atm_delta:
        return ATM
    if delta >= cfg.tested_delta:
        return TESTED
    return CALM


def _add_call_text(i: SpreadInput, cfg: DefenseSettings, lo: float, hi: float) -> str:
    return (f"añade un call spread: mismo vencimiento y mismo ancho ({i.width:g}) que el put spread, para no aumentar el riesgo máximo; call vendida de Δ {lo:g}–{hi:g} "
            f"y un crédito de al menos el {cfg.spread_call_min_credit_pct:g} % del original (si es menor, no merece la pena). Mejor tras un rebote, nunca en plena caída")


def assess_spread(i: SpreadInput, cfg: DefenseSettings) -> Optional[Defense]:
    """Defensa aconsejada para un put spread. Referencias de estilo tastylive, no reglas fijas: con más de 30 DTE hay tiempo (esperar o añadir un call spread
    si el IV Rank paga; rolar casi nunca compensa); de 21 a 30 DTE es la zona de gestión (rolar con crédito); por debajo de 21 manda el gamma
    (rolar ya con crédito o cerrar); por debajo de 7 se cierra según dónde esté el precio. None sin precio del subyacente."""
    if i.underlying is None or i.underlying <= 0:
        return None
    delta = i.delta if i.delta is not None else estimate_abs_delta(i.underlying, i.short_strike, i.dte, None, i.iv30, cfg.risk_free_rate)
    zone = spread_zone(delta, i.underlying, i.short_strike, i.long_strike, cfg)
    d = f"Δ {delta:.2f}" if delta is not None else "sin delta"
    high = i.iv_rank is not None and i.iv_rank >= cfg.ivr_good
    iv = "IV Rank alto" if high else "IV Rank bajo" if i.iv_rank is not None else "sin IV Rank (se trata como bajo)"
    steps: list[tuple[str, str]] = []

    def step(level: str, text: str) -> None:
        steps.append((level, text))

    lo, hi = cfg.call_delta_min, cfg.call_delta_max
    where = {CALM: "cómodo, fuera del dinero", TESTED: "testeado, acercándose al strike vendido", ATM: "en el strike vendido",
             BETWEEN: "entre los dos strikes", BEYOND: "por debajo de ambos strikes, cerca de la pérdida máxima"}[zone]
    head = f"{d}: {where} ({i.dte} DTE, {iv})"
    if 0 <= i.dte < cfg.spread_close_dte:                                       # < 7 DTE: se cierra según dónde esté el precio
        if i.long_strike < i.underlying <= i.short_strike:
            step("warn", f"{head}. Precio entre los strikes con menos de {cfg.spread_close_dte} días: cierra siempre; el riesgo de pin y de asignación no compensan.")
        elif i.underlying <= i.long_strike:
            step("warn", f"{head}. Precio por debajo de ambos strikes: cierra o deja que se ejerzan las dos patas, pero vigila tu margen.")
        else:
            step("info", f"{head}. Precio por encima del strike vendido: deja expirar o cierra por céntimos.")
    elif zone == CALM:
        if i.dte < cfg.gamma_dte:
            step("info", f"{head}: déjalo expirar o ciérralo al 50 % de beneficio.")
    elif i.dte > cfg.spread_manage_dte:                                          # hay tiempo
        if zone == TESTED:
            if high:
                step("warn", f"{head}: " + _add_call_text(i, cfg, lo, 0.25) + ".")
            else:
                step("info", f"{head}: espera.")
        elif zone == ATM:
            if high:
                step("warn", f"{head}: " + _add_call_text(i, cfg, 0.25, hi) + ".")
            else:
                step("info", f"{head}: espera, o " + _add_call_text(i, cfg, lo, hi) + ".")
        elif zone == BETWEEN:
            step("warn" if high else "info", f"{head}: " + ("" if high else "si hay prima, ") + _add_call_text(i, cfg, lo, hi) + ("" if high else "; si no, espera") + ".")
        else:
            step("info", f"{head}: " + ("espera o cierra" if high else "espera; cierra si la tesis está rota") + ".")
        step("info", "Con tanto tiempo por delante rolar casi nunca compensa: el tiempo aún juega a tu favor.")
    elif i.dte >= cfg.gamma_dte:                                                 # 21-30 DTE: zona de gestión
        if zone == TESTED:
            if high:
                step("warn", f"{head}: " + _add_call_text(i, cfg, lo, hi) + ".")
            else:
                step("info", f"{head}: espera a {cfg.gamma_dte} DTE.")
        elif zone == ATM:
            step("warn", f"{head}: " + ("rola abajo y fuera" if high else "rola fuera si da crédito") + " (crédito neto, nuevo vencimiento a "
                 f"{cfg.spread_roll_dte_min}–{cfg.spread_roll_dte_max} DTE).")
        elif zone == BETWEEN:
            step("warn", f"{head}: rola fuera con crédito" + ("" if high else "; si no hay crédito, cierra") + ".")
        else:
            step("warn", f"{head}: cierra o déjalo correr.")
    else:                                                                        # 7-21 DTE: manda el gamma
        if zone in (TESTED, ATM):
            step("warn", f"{head}: rola fuera YA con crédito; si no hay crédito, cierra.")
        elif zone == BETWEEN:
            step("warn", f"{head}: rola si queda extrínseco; si no, cierra.")
        else:
            step("warn", f"{head}: deja correr hasta la pérdida máxima o cierra; rolar ya no da crédito.")
    # cierres por pérdida, por eventos y por la tesis
    if i.entry and i.current is not None and i.entry > 0:
        loss = i.current - i.entry
        max_loss = i.width - i.entry
        if loss >= cfg.loss_multiple * i.entry or (max_loss > 0 and loss >= cfg.spread_close_loss_pct / 100 * max_loss):
            step("warn", f"Cierra: la pérdida ({loss:.2f} $/acción) ya es {loss / i.entry:.1f} veces el crédito cobrado"
                         + (f" y el {loss / max_loss * 100:.0f} % de la pérdida máxima" if max_loss > 0 else "")
                         + f" (límites: {cfg.loss_multiple:g}× el crédito o el {cfg.spread_close_loss_pct:g} % de la máxima).")
    if zone != CALM and i.next_earnings is not None and i.today is not None and i.today <= i.next_earnings <= i.expiry:
        step("warn", f"Valora cerrar: hay resultados el {i.next_earnings:%d/%m} antes del vencimiento y el spread está testeado.")
    if zone in (ATM, BETWEEN, BEYOND):
        step("info", "Si la tesis está rota (noticias fundamentales o ruptura de soportes clave), cierra sin esperar.")
    if zone != CALM and high and i.dte >= cfg.spread_close_dte:
        step("info", f"IV Rank {i.iv_rank:.0f} %: es el momento de cualquier defensa que venda prima.")
    return Defense(zone, delta, None, tuple(steps), i.delta is not None)


@dataclass(frozen=True)
class CallSpreadOption:
    """Call spread (call vendida + call comprada más arriba, mismo ancho que el put spread) del mismo vencimiento."""
    short_strike: float
    long_strike: float
    delta: float                     # |delta| de la call vendida
    credit: float                    # call vendida (bid) - call comprada (ask), por acción
    credit_pct: Optional[float]      # ... en % del crédito original del put spread
    ok: Optional[bool]               # cumple `spread_call_min_credit_pct` (None sin crédito original)
    new_credit: Optional[float]      # crédito total del iron condor resultante (put spread + call spread), por acción
    max_loss: Optional[float]        # riesgo máximo por acción del iron condor: ancho - crédito total (solo un lado puede perder)


def rank_call_spreads(width: float, entry: Optional[float], expiry: date, shorts: Sequence[RollQuote], longs: dict[float, RollQuote],
                      cfg: DefenseSettings) -> tuple[CallSpreadOption, ...]:
    """Call spreads candidatos de la defensa: call vendida de |delta| `call_delta_min`-`call_delta_max` (`shorts`) con la comprada `width` por encima
    (`longs`, por strike); crédito = bid de la vendida - ask de la comprada. Primero los que alcanzan `spread_call_min_credit_pct` del crédito original y,
    entre ellos, el de delta más cercana a la mitad de la banda. Como mucho tres. El ancho es el del put spread: el riesgo máximo no aumenta."""
    mid = (cfg.call_delta_min + cfg.call_delta_max) / 2
    out = []
    for q in shorts:
        if q.expiry != expiry or q.bid is None or q.bid <= 0 or q.delta is None or not cfg.call_delta_min - 1e-9 <= abs(q.delta) <= cfg.call_delta_max + 1e-9:
            continue
        long = longs.get(round(q.strike + width, 4))
        if long is None or long.ask is None or long.ask <= 0 or long.expiry != expiry:
            continue
        credit = q.bid - long.ask
        if credit <= 0:
            continue
        pct = credit / entry * 100 if entry else None
        ok = None if pct is None else pct >= cfg.spread_call_min_credit_pct - 1e-9
        total = (entry or 0.0) + credit
        out.append(CallSpreadOption(q.strike, long.strike, abs(q.delta), credit, pct, ok, total if entry else None, width - total if entry else None))
    out.sort(key=lambda o: (o.ok is not True, abs(o.delta - mid)))
    return tuple(out[:MAX_CANDIDATES])


# ---- protección (put comprada) y call (strangle) ------------------------------------------------------------------------------
MAX_CANDIDATES = 3


@dataclass(frozen=True)
class ProtectOption:
    """Put comprada del mismo vencimiento por debajo de la vendida: convierte la put en un spread y acota la pérdida."""
    strike: float
    ask: float
    delta: Optional[float]
    width: float                     # ancho del spread resultante
    debit_pct: Optional[float]       # débito en % del crédito original
    ok: Optional[bool]               # el débito no se come más de `protect_max_debit_pct` del crédito original (None sin crédito original)
    max_loss: Optional[float]        # pérdida máxima por acción del spread: ancho − (crédito original − débito)


@dataclass(frozen=True)
class CallOption:
    """Call vendida del mismo vencimiento (la put más la call forman un strangle)."""
    strike: float
    bid: float
    delta: float
    credit_pct: Optional[float]      # prima en % del crédito original
    ok: Optional[bool]               # compensa: al menos `call_min_credit_pct` del crédito original (None sin crédito original)
    breakeven_down: Optional[float]  # strike de la put − (crédito original + prima): lo que aguantas a la baja
    breakeven_up: Optional[float]    # strike de la call + (crédito original + prima): lo que aguantas al alza
    inverted: bool                   # call por debajo del strike de la put (straddle/strangle invertido)
    covered: Optional[bool]          # solo si es invertido: el crédito total cobrado supera la distancia entre strikes
    notes: tuple[str, ...]


def rank_protection(strike: float, expiry: date, entry: Optional[float], quotes: Sequence[RollQuote], cfg: DefenseSettings) -> tuple[ProtectOption, ...]:
    """Puts de protección candidatas: mismo vencimiento que la vendida y strike entre `protect_strike_pct_min` y `_max` % por debajo de ella; primero las
    que cumplen el débito máximo, y entre ellas la más cercana a la vendida (más protección). Como mucho tres."""
    lo, hi = strike * (1 - cfg.protect_strike_pct_max / 100), strike * (1 - cfg.protect_strike_pct_min / 100)
    out = []
    for q in quotes:
        if q.expiry != expiry or not lo - 1e-9 <= q.strike <= hi + 1e-9 or q.strike >= strike or q.ask is None or q.ask <= 0:
            continue
        pct = q.ask / entry * 100 if entry else None
        ok = None if pct is None else pct <= cfg.protect_max_debit_pct + 1e-9
        width = strike - q.strike
        out.append(ProtectOption(q.strike, q.ask, q.delta, width, pct, ok, width - (entry - q.ask) if entry else None))
    out.sort(key=lambda o: (o.ok is not True, -o.strike))
    return tuple(out[:MAX_CANDIDATES])


def rank_calls(strike: float, entry: Optional[float], expiry: date, quotes: Sequence[RollQuote], cfg: DefenseSettings,
               ex_dividend_in: Optional[int] = None, today: Optional[date] = None) -> tuple[CallOption, ...]:
    """Calls candidatas de la defensa: mismo vencimiento que la put y |delta| de `call_delta_min` a `call_delta_max`; las que cobran más de
    `call_min_credit_pct` del crédito original primero y, entre ellas, la más cercana a la mitad de la banda de delta. Como mucho tres. La prima es el
    bid (conservador). El breakeven nuevo supone mantener la put hasta el vencimiento."""
    mid = (cfg.call_delta_min + cfg.call_delta_max) / 2
    out = []
    for q in quotes:
        if q.expiry != expiry or q.bid is None or q.bid <= 0 or q.delta is None or not cfg.call_delta_min - 1e-9 <= abs(q.delta) <= cfg.call_delta_max + 1e-9:
            continue
        pct = q.bid / entry * 100 if entry else None
        ok = None if pct is None else pct >= cfg.call_min_credit_pct - 1e-9
        total = (entry or 0.0) + q.bid
        down = strike - total if entry else None
        up = q.strike + total if entry else None
        inverted = q.strike < strike
        covered = (total > strike - q.strike) if inverted and entry else None
        notes = []
        if inverted:
            notes.append("strangle invertido: " + ("el crédito total supera la distancia entre strikes" if covered else
                         "el crédito total NO supera la distancia entre strikes: pierdes con cualquier precio al vencimiento" if covered is False else "sin crédito original para comprobarlo"))
        if ex_dividend_in is not None and 0 <= ex_dividend_in <= cfg.ex_dividend_days:
            notes.append(f"ex-dividendo en {ex_dividend_in} días: la call podría asignarse antes")
        out.append(CallOption(q.strike, q.bid, abs(q.delta), pct, ok, down, up, inverted, covered, tuple(notes)))
    out.sort(key=lambda o: (o.ok is not True, abs(o.delta - mid)))
    return tuple(out[:MAX_CANDIDATES])


@dataclass(frozen=True)
class RollEvaluation:
    """Resultado de evaluar un roll con cotizaciones en vivo (lo que muestra el Panel)."""
    ticker: str
    expiry: date
    strike: float
    dte: int
    contracts: int
    underlying: Optional[float]
    entry: Optional[float]                 # crédito original por acción
    close_cost: float                      # lo que cuesta recomprar la put (ask en vivo; si no hay, su valor en la cartera)
    close_live: bool                       # True si `close_cost` es el ask en vivo
    delta: Optional[float]                 # |delta| actual (la del proveedor si hay cotización; si no, la estimada)
    iv_rank: Optional[float]
    defense: Optional[Defense]
    report: RollReport
    checked: int                           # cotizaciones candidatas pedidas
    quoted: int                            # ... de las que contestaron
    fetched_at: Optional[date] = None
    protections: tuple[ProtectOption, ...] = ()
    calls: tuple[CallOption, ...] = ()
    calls_quoted: int = 0                  # calls candidatas que contestaron (0 = el proveedor no las cotizó)
    kind: str = "put"                      # `put` (desnuda) | `spread` (put spread: `strike` es la vendida)
    long_strike: Optional[float] = None    # spread: put comprada
    call_spreads: tuple = ()               # spread: `CallSpreadOption` candidatos
