"""Estrategia de salida de cada perfil: cuándo recoger beneficios, cuándo revisar, stop de spreads, pin y qué hacer en el dinero.

Un perfil parte de un estilo base (`tastytrade` o `tpc`, sus valores salen de `ManagementSettings`) y guarda solo lo que cambia
(`Profile.exits`), así que un valor de config.yaml que el usuario no tocó sigue valiendo. `None` en un umbral lo apaga (p. ej. TPC no recoge
al 50 % ni revisa). Avisos orientativos, nunca órdenes. Funciones puras."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Optional

from scanner_opciones.config.settings import ManagementSettings

STYLES = ("tastytrade", "tpc")
ITM_ACTIONS = ("close", "hold")     # en el dinero: valorar cerrar (Tastytrade) o mantener sin rolar (TPC)


@dataclass(frozen=True)
class ExitRules:
    take_profit_pct: Optional[float]      # recoger beneficios al capturar este % de la prima (None = no se recoge antes del vencimiento)
    take_profit_max_pct: Optional[float]  # techo del rango de recogida: por encima, no esperar más
    review_dte: Optional[int]             # a ≤ N DTE sin haber llegado al objetivo: cerrar o rolar (None = no se revisa ni se rola)
    close_capture_pct: Optional[float]    # aviso informativo cuando ya está ganado este % de la prima (None = sin aviso)
    stop_multiple: Optional[float]        # spreads: stop cuando valen N veces el crédito cobrado (None = sin stop)
    pin_dte: int                          # spreads: riesgo de pin en los últimos N días...
    pin_buffer_pct: float                 # ...si el precio está a menos de este % del strike vendido
    itm_action: str                       # `close` | `hold`
    defense: bool = False                 # puts desnudas: avisos de defensa (testeada / en el dinero / muy dentro) y evaluación de rolls (`portfolio/defense.py`)
    # --- posiciones que no son puts vendidas (opciones compradas y debit spreads); las vendidas y los combos de crédito reutilizan los campos de arriba ---
    long_take_profit_pct: Optional[float] = None   # opción comprada: recoger al ganar este % de lo pagado (None = no se recoge antes)
    long_stop_pct: Optional[float] = None          # opción comprada o debit spread: stop al perder este % de lo pagado (None = sin stop)
    long_review_dte: Optional[int] = None          # opción comprada o debit spread: a ≤ N DTE sin haber llegado al objetivo, cerrar o rolar (None = no se revisa)
    debit_take_profit_pct: Optional[float] = None  # debit spread: recoger al capturar este % del beneficio máximo (None = no se recoge antes)

    @property
    def holds_to_expiry(self) -> bool:
        return self.take_profit_pct is None and self.review_dte is None


# Campos editables: (clave, etiqueta, tipo, apagable, explicación). `pct`/`int`/`mult` solo cambian la validación y la unidad mostrada.
FIELDS = (
    ("take_profit_pct", "Recoger beneficios al", "pct", True, "% de la prima cobrada ya ganado a partir del cual se avisa de recoger. Vacío = no se recoge antes del vencimiento."),
    ("take_profit_max_pct", "Techo del rango", "pct", True, "Por encima de este % no esperar más. Vacío = sin techo."),
    ("review_dte", "Revisar / rolar a ≤", "int", True, "DTE a partir del cual, sin haber llegado al objetivo, se avisa de cerrar o rolar. Vacío = no se revisa."),
    ("stop_multiple", "Stop del spread", "mult", True, "El spread vale N veces el crédito cobrado (2 = la pérdida iguala el crédito). Vacío = sin stop."),
    ("pin_dte", "Riesgo de pin: últimos", "int", False, "Días antes del vencimiento en los que se vigila el pin de un spread."),
    ("pin_buffer_pct", "Pin: distancia al strike", "pct", False, "% por encima del strike vendido por debajo del cual hay riesgo de pin."),
    ("long_take_profit_pct", "Opción comprada: recoger al", "pct", True, "% de lo pagado ya ganado a partir del cual se avisa de recoger (100 = duplicado). Vacío = no se recoge antes."),
    ("long_stop_pct", "Opción comprada / debit spread: stop al", "pct", True, "% de lo pagado perdido a partir del cual se avisa de cerrar. Vacío = sin stop."),
    ("long_review_dte", "Opción comprada / debit spread: revisar a ≤", "int", True, "DTE a partir del cual se avisa de cerrar o rolar (el valor temporal se evapora). Vacío = no se revisa."),
    ("debit_take_profit_pct", "Debit spread: recoger al", "pct", True, "% del beneficio máximo ya capturado a partir del cual se avisa de recoger. Vacío = no se recoge antes."),
)
KEYS = tuple(k for k, *_ in FIELDS) + ("itm_action", "defense")

# --- Estructura de la pantalla Salidas: reglas agrupadas por estrategia y el aviso que activa cada una ---
# Estrategias de las posiciones del Panel: (clave, nombre).
STRATEGIES = (("put", "Put vendida"), ("spread", "Spread de crédito"), ("call", "Call vendida / combo de crédito"),
              ("long", "Opción comprada"), ("debit", "Debit spread"), ("stock", "Acciones"))
STRATEGY_NAMES = dict(STRATEGIES)
SELL = ("put", "spread", "call")

# Reglas que no son un campo numérico de FIELDS: (clave, etiqueta, ayuda).
EXTRA_FIELDS = {
    "itm_action": ("En el dinero", "Qué se aconseja cuando el precio queda por debajo del strike vendido."),
    "defense": ("Defensa de la put", "Avisos de defensa de las puts desnudas (testeada, en el dinero, muy dentro, extrínseco, dividendo, resultados) y evaluación de "
                                     "rolls con cotizaciones en vivo desde el Panel. Un perfil que mantiene hasta el vencimiento (TPC) no rola."),
}

# Aviso que dispara cada regla: (nivel, texto corto). El nivel es el del aviso en el Panel (ok / info / warn).
ALERTS = {
    "take_profit_pct": ("ok", "Recoger beneficios: la prima ganada llega a este %."),
    "take_profit_max_pct": ("ok", "Recoger beneficios: «ya supera el rango, no esperes más»."),
    "review_dte": ("warn", "Revisar: quedan ≤ N días sin haber llegado al objetivo; cerrar o rolar cobrando crédito."),
    "itm_action": ("warn", "En el dinero: el precio pasó el strike vendido (valorar cerrar, o mantener sin rolar)."),
    "defense": ("warn", "Testeada / en el dinero / muy dentro, poco extrínseco, dividendo y resultados; evaluación de rolls."),
    "stop_multiple": ("warn", "Stop: el spread vale N veces el crédito cobrado."),
    "pin_dte": ("warn", "Riesgo de pin: quedan ≤ N días y el precio está cerca del strike vendido."),
    "pin_buffer_pct": ("warn", "Riesgo de pin: el precio está a ≤ este % por encima del strike vendido."),
    "long_take_profit_pct": ("ok", "Recoger beneficios: ganado este % de lo pagado."),
    "long_stop_pct": ("warn", "Stop: pierde este % de lo pagado; valorar cerrar."),
    "long_review_dte": ("warn", "Revisar: quedan ≤ N días sin llegar al objetivo; el valor temporal se evapora."),
    "debit_take_profit_pct": ("ok", "Recoger beneficios: capturado este % del beneficio máximo."),
}

# Umbrales de delta/DTE que acompañan a las reglas de un grupo: son de solo lectura aquí (salen de `management.defense` de config.yaml, comunes a
# todos los perfiles): (atributo de DefenseSettings, etiqueta, unidad, aviso que dispara).
DELTA_INFO = {
    "put": (
        ("tested_delta", "Δ testeada desde", "Δ", "Testeada: aún fuera del dinero; esperar o vender la call, rolar rinde poco."),
        ("atm_delta", "Δ en el dinero desde", "Δ", "En el dinero: rolar abajo y fuera con crédito neto."),
        ("deep_delta", "Δ muy dentro desde", "Δ", "Muy dentro: rolar solo hacia fuera o valorar la asignación."),
        ("gamma_dte", "Gamma: gestionar a ≤", "DTE", "A ≤ N DTE el gamma se acelera: gestionar aunque la pérdida no sea extrema."),
    ),
    "credit": (
        ("spread_atm_delta", "Δ vendida: en el strike desde", "Δ", "El precio está en el strike vendido: la zona del spread sube y cambia el aviso (esperar, call spread, rolar o cerrar)."),
        ("spread_between_delta", "Δ vendida: entre strikes desde", "Δ", "El precio está entre los dos strikes."),
        ("spread_beyond_delta", "Δ vendida: más allá de ambos desde", "Δ", "Cerca de la pérdida máxima."),
        ("spread_manage_dte", "Zona de gestión: de gamma a", "DTE", "Con más DTE hay tiempo (rolar casi nunca compensa); por debajo se gestiona según la zona."),
        ("spread_close_dte", "Cerrar según los strikes a <", "DTE", "Con menos DTE se cierra según dónde esté el precio respecto a los strikes."),
    ),
    "buy": (
        ("tested_delta", "Δ cerca del dinero desde", "Δ", "Zonas invertidas: más delta = más dentro del dinero = a favor (lejos < cerca < en el dinero < muy dentro)."),
        ("atm_delta", "Δ en el dinero desde", "Δ", "En el dinero (a favor)."),
        ("deep_delta", "Δ muy dentro desde", "Δ", "Muy dentro del dinero (a favor)."),
    ),
}

# Grupos en el orden en que se muestran: (id, título, estrategias a las que aplica, claves editables)..
GROUPS = (
    ("sell", "Venta de opciones · reglas comunes", SELL, ("take_profit_pct", "take_profit_max_pct", "review_dte", "itm_action")),
    ("put", "Solo put vendida", ("put",), ("defense",)),
    ("credit", "Spreads y combos de crédito", ("spread", "call"), ("stop_multiple", "pin_dte", "pin_buffer_pct")),
    ("buy", "Compra de opciones · reglas comunes", ("long", "debit"), ("long_stop_pct", "long_review_dte")),
    ("long", "Solo opción comprada", ("long",), ("long_take_profit_pct",)),
    ("debit", "Solo debit spread", ("debit",), ("debit_take_profit_pct",)),
)


def sections(active: set[str]) -> list[dict]:
    """Grupos de reglas para una tarjeta: `on` = el perfil admite alguna de las estrategias del grupo (si no, se muestra en gris).
    `active` son las estrategias del perfil."""
    out = []
    for gid, title, strategies, keys in GROUPS:
        out.append(dict(id=gid, title=title, rows=keys, info=DELTA_INFO.get(gid, ()), on=bool(active & set(strategies)),
                        strategies=[(k, STRATEGY_NAMES[k], k in active) for k in strategies]))
    return out


# Estilos base propios del usuario: {nombre: (estilo incorporado del que parten, cambios sobre él)}. Los carga `AppService` desde `meta` al
# arrancar y al guardar (estado de proceso, como la configuración); las funciones de abajo los tratan igual que a los incorporados.
_CUSTOM: dict[str, tuple[str, dict]] = {}
STYLE_LABELS = {"tastytrade": "Tastytrade", "tpc": "TPC"}


def set_custom_styles(styles: dict[str, tuple[str, dict]]) -> None:
    """Sustituye los estilos base propios (lo hace el servicio al leer o guardar `meta.exit_styles`)."""
    _CUSTOM.clear()
    _CUSTOM.update(styles)


def custom_styles() -> dict[str, tuple[str, dict]]:
    return dict(_CUSTOM)


def is_style(name: str) -> bool:
    return name in STYLES or name in _CUSTOM


def all_styles() -> tuple[str, ...]:
    """Los estilos incorporados y los propios, en ese orden."""
    return STYLES + tuple(_CUSTOM)


def style_label(name: str) -> str:
    return STYLE_LABELS.get(name, name)


def root_style(name: str) -> str:
    """Estilo incorporado del que parte `name` (él mismo si ya lo es)."""
    return name if name in STYLES else _CUSTOM[name][0] if name in _CUSTOM else "tastytrade"


def validate_style_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("el estilo base necesita un nombre")
    if len(name) > 40:
        raise ValueError("el nombre del estilo base es demasiado largo (máx. 40)")
    if name.casefold() in {s.casefold() for s in STYLES}:
        raise ValueError(f"«{name}» es un estilo incorporado: elige otro nombre")
    return name


def dump_custom(styles: dict[str, tuple[str, dict]]) -> str:
    return json.dumps({n: {"parent": parent, "exits": ov} for n, (parent, ov) in styles.items()})


def load_custom(raw: str) -> dict[str, tuple[str, dict]]:
    """Estilos propios de un JSON guardado (ignora lo que no se lee o parte de un estilo desconocido)."""
    try:
        data = json.loads(raw) if raw else {}
        return {str(n): (str(d["parent"]), check_overrides(d.get("exits") or {})) for n, d in data.items() if d.get("parent") in STYLES}
    except Exception:
        return {}


def defaults(style: str, cfg: ManagementSettings) -> ExitRules:
    """Reglas de un estilo base (incorporado o propio) con los valores de `management` de la configuración."""
    if style in _CUSTOM:
        parent, ov = _CUSTOM[style]
        return replace(defaults(parent, cfg), **{k: v for k, v in ov.items() if k in KEYS})
    if style == "tpc":
        return ExitRules(None, None, None, cfg.tpc_close_capture_pct, None, cfg.pin_dte, cfg.pin_buffer_pct,
                         "hold", False)
    return ExitRules(cfg.take_profit_pct, cfg.take_profit_max_pct, cfg.review_dte, None, cfg.spread_stop_value_multiple, cfg.pin_dte,
                     cfg.pin_buffer_pct, "close", True,
                     cfg.long_take_profit_pct, cfg.long_stop_pct, cfg.long_review_dte, cfg.debit_take_profit_pct)


def resolve(style: str, overrides: dict, cfg: ManagementSettings) -> ExitRules:
    """Reglas efectivas: las del estilo con los cambios del perfil encima (una clave ausente = la del estilo; un valor None = apagado)."""
    base = defaults(style, cfg)
    return replace(base, **{k: v for k, v in overrides.items() if k in KEYS})


def validate(rules: ExitRules) -> ExitRules:
    """`ValueError` con un mensaje para el usuario si las reglas no tienen sentido."""
    if rules.take_profit_pct is not None and not 0 < rules.take_profit_pct <= 100:
        raise ValueError("«Recoger beneficios al» tiene que estar entre 0 y 100 %")
    if rules.take_profit_max_pct is not None:
        if rules.take_profit_pct is None:
            raise ValueError("el techo del rango necesita un «Recoger beneficios al»")
        if not rules.take_profit_pct <= rules.take_profit_max_pct <= 100:
            raise ValueError("el techo del rango no puede ser menor que el objetivo ni superar 100 %")
    if rules.review_dte is not None and rules.review_dte < 0:
        raise ValueError("«Revisar a ≤» no puede ser negativo")
    if rules.close_capture_pct is not None and not 0 < rules.close_capture_pct <= 100:
        raise ValueError("«Aviso de prima casi ganada» tiene que estar entre 0 y 100 %")
    if rules.stop_multiple is not None and rules.stop_multiple <= 1:
        raise ValueError("el stop del spread tiene que ser mayor que 1× el crédito")
    if rules.pin_dte < 0 or rules.pin_buffer_pct < 0:
        raise ValueError("los valores del pin no pueden ser negativos")
    if rules.long_take_profit_pct is not None and rules.long_take_profit_pct <= 0:
        raise ValueError("«Opción comprada: recoger al» tiene que ser positivo")
    if rules.long_stop_pct is not None and not 0 < rules.long_stop_pct <= 100:
        raise ValueError("el stop de la opción comprada tiene que estar entre 0 y 100 %")
    if rules.long_review_dte is not None and rules.long_review_dte < 0:
        raise ValueError("«Opción comprada: revisar a ≤» no puede ser negativo")
    if rules.debit_take_profit_pct is not None and not 0 < rules.debit_take_profit_pct <= 100:
        raise ValueError("«Debit spread: recoger al» tiene que estar entre 0 y 100 %")
    if rules.itm_action not in ITM_ACTIONS:
        raise ValueError("acción en el dinero no válida")
    return rules


def overrides_of(style: str, rules: ExitRules, cfg: ManagementSettings) -> dict:
    """Solo lo que `rules` cambia respecto al estilo base (lo que se guarda en el perfil)."""
    base = defaults(style, cfg)
    return {f.name: getattr(rules, f.name) for f in fields(rules) if getattr(rules, f.name) != getattr(base, f.name)}


def check_overrides(raw: dict) -> dict:
    """Limpia lo leído de un JSON guardado: solo claves conocidas y valores numéricos, None o texto de la acción en el dinero."""
    out: dict = {}
    for k, v in raw.items():
        if k == "itm_action":
            if v in ITM_ACTIONS:
                out[k] = v
        elif k == "defense":
            if isinstance(v, bool):
                out[k] = v
        elif k in KEYS and (v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))):
            out[k] = v
    return out


def describe(r: ExitRules) -> list[str]:
    """Frases que explican lo que hace la estrategia de salida (para la pantalla Salidas)."""
    out: list[str] = []
    if r.take_profit_pct is not None:
        rng = f" (rango {r.take_profit_pct:g}–{r.take_profit_max_pct:g} %)" if r.take_profit_max_pct else ""
        out.append(f"Recoger beneficios al ganar el {r.take_profit_pct:g} % de la prima{rng}: recomprar la put y liberar el capital.")
    else:
        out.append("No se recoge antes del vencimiento: se mantiene hasta el final.")
    if r.review_dte is not None:
        goal = f" sin haber llegado al {r.take_profit_pct:g} %" if r.take_profit_pct is not None else ""
        out.append(f"A ≤ {r.review_dte} DTE{goal}: cerrar o rolar a un vencimiento posterior cobrando crédito.")
    else:
        out.append("No se revisa ni se rola por tiempo.")
    if r.stop_multiple is not None:
        out.append(f"Spreads: stop cuando el spread vale {r.stop_multiple:g} veces el crédito cobrado.")
    else:
        out.append("Spreads: sin stop (la pérdida máxima es el ancho menos el crédito).")
    out.append(f"Spreads: riesgo de pin en los últimos {r.pin_dte} días si el precio está a ≤ {r.pin_buffer_pct:g} % del strike vendido.")
    out.append("En el dinero: " + ("valorar cerrar antes que esperar la asignación." if r.itm_action == "close"
                                  else "no se rola; si te asignan, reevaluar la tesis y valorar vender calls."))
    if r.long_take_profit_pct is not None:
        out.append(f"Opción comprada: recoger al ganar el {r.long_take_profit_pct:g} % de lo pagado.")
    if r.long_stop_pct is not None:
        out.append(f"Opción comprada y debit spread: stop al perder el {r.long_stop_pct:g} % de lo pagado.")
    if r.long_review_dte is not None:
        out.append(f"Opción comprada y debit spread: a ≤ {r.long_review_dte} DTE, cerrar o rolar (el valor temporal se evapora).")
    if r.debit_take_profit_pct is not None:
        out.append(f"Debit spread: recoger al capturar el {r.debit_take_profit_pct:g} % del beneficio máximo.")
    if r.long_take_profit_pct is None and r.long_stop_pct is None and r.long_review_dte is None and r.debit_take_profit_pct is None:
        out.append("Opciones compradas y debit spreads: se mantienen hasta el vencimiento (sin objetivo, stop ni revisión).")
    if r.defense:
        out.append("Defensa de las puts desnudas según su delta, los días y el IV Rank: testeada (Δ 0,30+) esperar o vender la call; en el dinero (Δ 0,50+) rolar "
                   "abajo y fuera con crédito neto; muy dentro (Δ 0,70+) rolar solo hacia fuera o asignación; a ≤ 21 DTE o sin extrínseco, gestionar ya. "
                   "Spreads: matriz de la delta de la vendida × DTE (> 30, 21–30, < 21, < 7) × IV Rank (esperar, añadir un call spread del mismo ancho, rolar con "
                   "crédito o cerrar). Evaluación de rolls y de las defensas con cotizaciones en vivo desde el Panel.")
    else:
        out.append("Sin avisos de defensa: no se rola ni se defiende la put.")
    return out


def style_summary(r: ExitRules) -> dict:
    """Datos para dibujar un estilo base en la pantalla Salidas (comparación visual): hitos de una put vendida de ~45 DTE y reglas en pocas palabras."""
    return dict(
        take_profit=r.take_profit_pct, take_profit_max=r.take_profit_max_pct, review_dte=r.review_dte, stop=r.stop_multiple,
        hold=r.holds_to_expiry, itm=r.itm_action, defense=r.defense, pin_dte=r.pin_dte, pin_buffer=r.pin_buffer_pct,
        long_on=any(v is not None for v in (r.long_take_profit_pct, r.long_stop_pct, r.long_review_dte, r.debit_take_profit_pct)),
        long_tp=r.long_take_profit_pct, long_stop=r.long_stop_pct, long_review=r.long_review_dte, debit_tp=r.debit_take_profit_pct,
        close_capture=r.close_capture_pct)


def _show(key: str, v) -> str:
    if v is None:
        return "apagado"
    if key == "itm_action":
        return "valorar cerrar" if v == "close" else "mantener, no rolar"
    if key == "defense":
        return "activada" if v else "desactivada"
    return f"{v:g}"


def diff_rules(base: ExitRules, rules: ExitRules) -> list[tuple[str, str, str]]:
    """Qué cambia `rules` respecto a `base`: [(regla, valor del estilo, valor del perfil)]; vacío = sin modificaciones."""
    labels = {k: lab for k, lab, *_ in FIELDS} | {k: lab for k, (lab, _) in EXTRA_FIELDS.items()}
    return [(labels[k], _show(k, getattr(base, k)), _show(k, getattr(rules, k))) for k in KEYS if getattr(base, k) != getattr(rules, k)]
