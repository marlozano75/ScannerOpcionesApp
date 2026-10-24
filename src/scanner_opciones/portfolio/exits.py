"""Estrategia de salida de cada perfil: cuándo recoger beneficios, cuándo revisar, stop de spreads, pin, qué hacer en el dinero y el peso por acción.

Un perfil parte de un estilo base (`tastytrade` o `tpc`, sus valores salen de `ManagementSettings`) y guarda solo lo que cambia
(`Profile.exits`), así que un valor de config.yaml que el usuario no tocó sigue valiendo. `None` en un umbral lo apaga (p. ej. TPC no recoge
al 50 % ni revisa). Avisos orientativos, nunca órdenes. Funciones puras."""
from __future__ import annotations

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
    warn_position_pct: float              # peso por acción tras asignación sobre el NLV: aviso
    max_position_pct: float               # ... y alerta
    itm_action: str                       # `close` | `hold`
    defense: bool = False                 # puts desnudas: avisos de defensa (testeada / en el dinero / muy dentro) y evaluación de rolls (`portfolio/defense.py`)

    @property
    def holds_to_expiry(self) -> bool:
        return self.take_profit_pct is None and self.review_dte is None


# Campos editables: (clave, etiqueta, tipo, apagable, explicación). `pct`/`int`/`mult` solo cambian la validación y la unidad mostrada.
FIELDS = (
    ("take_profit_pct", "Recoger beneficios al", "pct", True, "% de la prima cobrada ya ganado a partir del cual se avisa de recoger. Vacío = no se recoge antes del vencimiento."),
    ("take_profit_max_pct", "Techo del rango", "pct", True, "Por encima de este % no esperar más. Vacío = sin techo."),
    ("review_dte", "Revisar / rolar a ≤", "int", True, "DTE a partir del cual, sin haber llegado al objetivo, se avisa de cerrar o rolar. Vacío = no se revisa."),
    ("close_capture_pct", "Aviso de prima casi ganada", "pct", True, "Aviso informativo cuando queda poca prima por ganar: cerrar y liberar la obligación o seguir hasta el vencimiento. Vacío = sin aviso."),
    ("stop_multiple", "Stop del spread", "mult", True, "El spread vale N veces el crédito cobrado (2 = la pérdida iguala el crédito). Vacío = sin stop."),
    ("pin_dte", "Riesgo de pin: últimos", "int", False, "Días antes del vencimiento en los que se vigila el pin de un spread."),
    ("pin_buffer_pct", "Pin: distancia al strike", "pct", False, "% por encima del strike vendido por debajo del cual hay riesgo de pin."),
    ("warn_position_pct", "Peso por acción: aviso", "pct", False, "% del NLV que supondría la acción si te asignan todas sus puts."),
    ("max_position_pct", "Peso por acción: alerta", "pct", False, "% del NLV a partir del cual se da alerta."),
)
KEYS = tuple(k for k, *_ in FIELDS) + ("itm_action", "defense")


def defaults(style: str, cfg: ManagementSettings) -> ExitRules:
    """Reglas de un estilo base con los valores de `management` de la configuración."""
    if style == "tpc":
        return ExitRules(None, None, None, cfg.tpc_close_capture_pct, None, cfg.pin_dte, cfg.pin_buffer_pct,
                         cfg.tpc_warn_position_pct, cfg.tpc_max_position_pct, "hold", False)
    return ExitRules(cfg.take_profit_pct, cfg.take_profit_max_pct, cfg.review_dte, None, cfg.spread_stop_value_multiple, cfg.pin_dte,
                     cfg.pin_buffer_pct, cfg.warn_position_pct, cfg.max_position_pct, "close", True)


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
    if not 0 < rules.warn_position_pct <= rules.max_position_pct:
        raise ValueError("el aviso de peso por acción tiene que ser positivo y no superar la alerta")
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
    if r.close_capture_pct is not None:
        out.append(f"Con el {r.close_capture_pct:g} % de la prima ganada: aviso informativo (cerrar y liberar la obligación o seguir hasta el vencimiento).")
    if r.stop_multiple is not None:
        out.append(f"Spreads: stop cuando el spread vale {r.stop_multiple:g} veces el crédito cobrado.")
    else:
        out.append("Spreads: sin stop (la pérdida máxima es el ancho menos el crédito).")
    out.append(f"Spreads: riesgo de pin en los últimos {r.pin_dte} días si el precio está a ≤ {r.pin_buffer_pct:g} % del strike vendido.")
    out.append("En el dinero: " + ("valorar cerrar antes que esperar la asignación." if r.itm_action == "close"
                                  else "no se rola; si te asignan, reevaluar la tesis y valorar vender calls."))
    if r.defense:
        out.append("Defensa de las puts desnudas según su delta, los días y el IV Rank: testeada (Δ 0,30+) esperar o vender la call; en el dinero (Δ 0,50+) rolar "
                   "abajo y fuera con crédito neto; muy dentro (Δ 0,70+) rolar solo hacia fuera o asignación; a ≤ 21 DTE o sin extrínseco, gestionar ya. "
                   "Evaluación de rolls con cotizaciones en vivo desde el Panel.")
    else:
        out.append("Sin avisos de defensa: no se rola ni se defiende la put.")
    out.append(f"Peso por acción tras asignación: aviso por encima del {r.warn_position_pct:g} % del NLV y alerta por encima del {r.max_position_pct:g} %.")
    return out
