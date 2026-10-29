"""Perfiles del Scanner: un perfil es una «foto» de los filtros del formulario (los parámetros de la URL) para cada instrumento.

· `put` y `spread` son los parámetros de la URL de cada juego de filtros (activados, valores, precio de venta, calidad, tendencia…): cargar el
  perfil REEMPLAZA todo el formulario (lo que no está en la foto queda desactivado). Se guardan desde el propio Scanner («Guardar en el perfil»).
· `instruments` es el instrumento del perfil (put vendida o spread de puts: uno solo).
Funciones puras; el almacenamiento (JSON en `meta`) lo hace `AppService`."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from scanner_opciones.portfolio import exits as exits_mod

INSTRUMENTS = ("put", "spread")
FALLBACK_NAME = "Tortuga"      # nombre de origen del perfil de las puts que no encajan por DTE (se puede renombrar: lo identifica `Profile.fallback`)
# claves de la URL que no son filtros de un perfil (navegación, estado de la página o identidad del perfil)
NOT_FILTERS = frozenset(("profile", "reset", "debug", "message", "submitted", "mode", "set"))


@dataclass(frozen=True)
class Profile:
    name: str
    instruments: tuple[str, ...] = ("put",)
    put: dict = field(default_factory=dict)       # parámetros de la URL del juego de filtros de put
    spread: dict = field(default_factory=dict)    # ... y del de spread
    management: str = "tastytrade"                # reglas con las que se gestionan en el Panel las puts de este perfil: tastytrade | tpc
    color: str = ""                               # color del botón del perfil y de su etiqueta en el Panel (#rrggbb; vacío = sin color)
    exits: dict = field(default_factory=dict)     # estrategia de salida: solo lo que cambia respecto al estilo `management` (`portfolio.exits`)
    fallback: bool = False                        # perfil de las puts que no encajan por DTE: no se borra ni se clasifica por DTE, pero sí se renombra

    def exit_rules(self, cfg) -> exits_mod.ExitRules:
        """Estrategia de salida efectiva: la del estilo de gestión con los cambios de este perfil."""
        return exits_mod.resolve(self.management, self.exits, cfg)

    @property
    def mode(self) -> str:
        """Instrumento del perfil (`put` o `spread`): con el que arranca el Scanner al cargarlo."""
        return self.instruments[0]

    def params(self, mode: Optional[str] = None) -> dict:
        """Parámetros del juego de filtros de un instrumento (`put` o `spread`; por defecto el del perfil)."""
        return self.set_params(mode or self.mode)

    def set_params(self, name: str) -> dict:
        """Parámetros de la URL del juego de filtros `put` o `spread`."""
        return dict(self.spread if name == "spread" else self.put)

    def with_sets(self, put: dict, spread: dict) -> "Profile":
        """Copia con los dos juegos de filtros sustituidos (sin las claves que no son filtros ni los `o_...` del otro juego)."""
        def clean(params: dict) -> dict:
            return {k: str(v) for k, v in params.items() if k not in NOT_FILTERS and not k.startswith("o_")}
        return replace(self, put=clean(put), spread=clean(spread))

    def with_filters(self, mode: str, params: dict) -> "Profile":
        """Copia con el juego de filtros del instrumento `mode` sustituido por `params` (sin las claves que no son filtros)."""
        clean = {k: str(v) for k, v in params.items() if k not in NOT_FILTERS}
        return replace(self, spread=clean) if mode == "spread" else replace(self, put=clean)

    def to_dict(self) -> dict:
        return {"name": self.name, "instruments": list(self.instruments), "put": self.put, "spread": self.spread, "management": self.management,
                "color": self.color, "exits": self.exits, "fallback": self.fallback}


def validate_profile(p: Profile) -> Profile:
    name = p.name.strip()
    if not name:
        raise ValueError("el perfil necesita un nombre")
    instruments = tuple(i for i in INSTRUMENTS if i in p.instruments)
    if len(instruments) != 1:
        raise ValueError(f"«{name}»: elige un instrumento (put o spread)")
    if p.color and not re.fullmatch(r"#[0-9a-fA-F]{6}", p.color):
        raise ValueError(f"«{name}»: color no válido")
    if p.management not in ("tastytrade", "tpc"):
        raise ValueError(f"«{name}»: gestión no válida")
    return replace(p, name=name, instruments=instruments)


def split_dual(profiles: list[Profile], default: Optional[str] = None) -> tuple[list[Profile], Optional[str], dict[str, str]]:
    """Un perfil es de UN instrumento: el que tiene put y spread activos se divide en «X (put)» y «X (spread)», cada uno con los filtros de su
    instrumento (el perfil de las puts que no encajan se queda solo como put). Devuelve (perfiles, inicial, {nombre antiguo: nombre de su put})."""
    out: list[Profile] = []
    renames: dict[str, str] = {}
    for p in profiles:
        ins = tuple(i for i in INSTRUMENTS if i in p.instruments)
        if len(ins) < 2:
            out.append(p)
        elif p.fallback or p.name == FALLBACK_NAME:
            out.append(replace(p, instruments=("put",), spread={}))
        else:
            renames[p.name] = f"{p.name} (put)"
            out.append(replace(p, name=f"{p.name} (put)", instruments=("put",), spread={}))
            out.append(replace(p, name=f"{p.name} (spread)", instruments=("spread",), put={}))
    return out, renames.get(default, default), renames


def validate_profiles(profiles: list[Profile], default: Optional[str]) -> tuple[list[Profile], str]:
    profiles, default, _ = split_dual(profiles, default)
    out = [validate_profile(p) for p in profiles]
    if not out:
        raise ValueError("tiene que quedar al menos un perfil")
    if not any(p.fallback for p in out):      # un perfil «Tortuga» sin marcar (guardado antes de poder renombrarlo) es el de las puts que no encajan
        out = [replace(p, fallback=True) if p.name == FALLBACK_NAME else p for p in out]
    names = [p.name for p in out]
    if len(set(n.casefold() for n in names)) != len(names):
        raise ValueError("dos perfiles no pueden llamarse igual")
    return out, (default if default in names else names[0])


def dump(profiles: list[Profile], default: str) -> str:
    return json.dumps({"profiles": [p.to_dict() for p in profiles], "default": default})


def parse(raw: str) -> Optional[tuple[list[Profile], Optional[str]]]:
    """(perfiles, perfil inicial) de un JSON guardado tal cual, sin validar ni dividir; None si no hay o no se lee."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        profiles = [Profile(d["name"], tuple(d["instruments"]), {str(k): str(v) for k, v in d["put"].items()},
                            {str(k): str(v) for k, v in d["spread"].items()}, d.get("management", "tastytrade"), d.get("color", ""),
                            exits_mod.check_overrides(d.get("exits") or {}), bool(d.get("fallback", d["name"] == FALLBACK_NAME))) for d in data["profiles"]]
        return profiles, data.get("default")
    except Exception:
        return None


def load(raw: str) -> Optional[tuple[list[Profile], str]]:
    """(perfiles, perfil inicial) de un JSON guardado, o None si no hay o ya no es válido. Los perfiles de dos instrumentos se dividen."""
    parsed = parse(raw)
    if parsed is None:
        return None
    try:
        return validate_profiles(*parsed)
    except Exception:
        return None


# valores que equivalen a «sin valor» al comparar el formulario con un perfil guardado (una clave ausente es lo mismo que estas)
_EMPTY = frozenset(("", "any", "off"))
SMALL_CHANGE = 0.25      # variación relativa máxima de un valor numérico para marcarla como cambio pequeño


def profile_diff(saved: dict, current: dict) -> dict[str, str]:
    """Claves del formulario que difieren del perfil guardado: `warn` (valor numérico que varía poco, hasta `SMALL_CHANGE` en relativo) o
    `bad` (cualquier otro cambio: filtro marcado o desmarcado, opción distinta o variación grande). Una clave ausente equivale a vacío."""
    def norm(params: dict, key: str) -> str:
        v = str(params.get(key, "")).strip()
        return "" if v in _EMPTY or (key == "signals" and v == "0") else v
    out: dict[str, str] = {}
    for key in set(saved) | set(current):
        if key in NOT_FILTERS or key.startswith("o_"):
            continue
        a, b = norm(saved, key), norm(current, key)
        if a == b:
            continue
        try:
            x, y = float(a), float(b)
        except ValueError:
            out[key] = "bad"
            continue
        if x == y:
            continue
        top = max(abs(x), abs(y))
        out[key] = "warn" if top and abs(x - y) / top <= SMALL_CHANGE else "bad"
    return out
