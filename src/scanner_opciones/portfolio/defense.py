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
ZONE_LABELS = {CALM: "tranquila", TESTED: "testeada", ATM: "en el dinero", DEEP: "muy dentro del dinero"}


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


@dataclass(frozen=True)
class RollReport:
    best: tuple[RollOption, ...]     # el mejor strike de cada vencimiento, por plazo
    verdict: str                     # `roll` | `extend` | `none`
    text: str


def rank_rolls(strike: float, entry: Optional[float], close_cost: float, quotes: Sequence[RollQuote], today: date, cfg: DefenseSettings,
               ) -> RollReport:
    """Mejor roll de la put: del strike actual hacia abajo y con vencimiento posterior, SIEMPRE con crédito neto. `close_cost`: lo que cuesta recomprar la
    put actual (su ask). Por cada vencimiento se elige el strike más bajo que da el crédito mínimo (el que más se aleja), con preferencia por
    una |delta| final de 0,30–0,40. Si en 30–60 DTE no hay crédito, se alarga hasta `roll_dte_far`; si tampoco, la defensa no es viable."""
    need_pct = entry * cfg.roll_min_credit_pct / 100 if entry else 0.0
    need = max(cfg.roll_min_credit, need_pct)
    options: dict[date, list[RollOption]] = {}
    for q in quotes:
        dte = (q.expiry - today).days
        if q.bid is None or q.bid <= 0 or q.strike > strike + 1e-9 or dte < cfg.roll_dte_min or dte > cfg.roll_dte_far:
            continue
        net = q.bid - close_cost
        notes = []
        if dte > cfg.roll_dte_max:
            notes.append(f"{dte} DTE: más allá de {cfg.roll_dte_max} se ata mucho tiempo por poco crédito extra")
        if q.delta is not None and q.delta > cfg.atm_delta:
            notes.append(f"Δ {q.delta:.2f} sigue por encima de {cfg.atm_delta:g}: solo compras tiempo, no probabilidad")
        elif q.delta is not None and q.delta > cfg.roll_target_delta_max:
            notes.append(f"Δ {q.delta:.2f} por encima del objetivo ({cfg.roll_target_delta_min:g}–{cfg.roll_target_delta_max:g})")
        pct = net / entry * 100 if entry else None
        options.setdefault(q.expiry, []).append(RollOption(q.expiry, dte, q.strike, q.bid, q.delta, net, pct, net >= need - 1e-9, tuple(notes)))
    best: list[RollOption] = []
    for expiry in sorted(options):
        rows = options[expiry]
        good = [o for o in rows if o.ok]
        pool = [o for o in good if o.delta is None or o.delta <= cfg.roll_target_delta_max] or good
        if pool:
            best.append(min(pool, key=lambda o: o.strike))                 # el strike más bajo que mantiene el crédito mínimo
        else:
            best.append(max((o for o in rows if o.net_credit > 0), key=lambda o: o.net_credit, default=max(rows, key=lambda o: o.net_credit)))
    in_range = [o for o in best if o.ok and o.dte <= cfg.roll_dte_max]
    extended = [o for o in best if o.ok and o.dte > cfg.roll_dte_max]
    if in_range:
        top = min(in_range, key=lambda o: (o.strike, o.dte))
        return RollReport(tuple(best), "roll", f"Roll viable: {top.expiry:%d/%m/%Y} ({top.dte} DTE) al strike {top.strike:g} cobra {top.net_credit:.2f} $/acción neto.")
    if extended:
        top = min(extended, key=lambda o: o.dte)
        return RollReport(tuple(best), "extend", f"Solo hay crédito alargando el plazo: {top.expiry:%d/%m/%Y} ({top.dte} DTE) al strike {top.strike:g} "
                                                 f"cobra {top.net_credit:.2f} $/acción neto.")
    return RollReport(tuple(best), "none", "No hay roll con crédito neto suficiente ni alargando el plazo: la defensa no es viable. Cierra la put o acepta la asignación "
                                           "(si te gusta el valor a ese precio y la posición cabe en la cartera).")


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
