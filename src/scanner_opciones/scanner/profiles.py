"""Perfiles del Scanner: un perfil es una «foto» de los filtros del formulario (los parámetros de la URL) para cada instrumento.

· `put` y `spread` son los parámetros de la URL de cada juego de filtros (activados, valores, precio de venta, calidad, tendencia…): cargar el
  perfil REEMPLAZA todo el formulario (lo que no está en la foto queda desactivado). Se guardan desde el propio Scanner («Guardar en el perfil»).
· `instruments` son los instrumentos que el perfil tiene activos (put vendida, spread de puts o ambos).
Funciones puras; el almacenamiento (JSON en `meta`) lo hace `AppService`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Optional

INSTRUMENTS = ("put", "spread")
# claves de la URL que no son filtros de un perfil (navegación, estado de la página o identidad del perfil)
NOT_FILTERS = frozenset(("profile", "reset", "debug", "message", "submitted", "mode", "set"))


@dataclass(frozen=True)
class Profile:
    name: str
    instruments: tuple[str, ...] = ("put",)
    put: dict = field(default_factory=dict)       # parámetros de la URL del juego de filtros de put
    spread: dict = field(default_factory=dict)    # ... y del de spread
    management: str = "tastytrade"                # reglas con las que se gestionan en el Panel las puts de este perfil: tastytrade | tpc

    @property
    def mode(self) -> str:
        """Instrumento con el que arranca el Scanner al cargarlo: `both` si están activos los dos."""
        return "both" if len(self.instruments) > 1 else self.instruments[0]

    def params(self, mode: Optional[str] = None) -> dict:
        """Parámetros del juego de filtros de un instrumento (`put`, `spread` o `both`; el modo «Ambos» usa de momento el de spread)."""
        mode = mode or self.mode
        return dict(self.spread if mode in ("spread", "both") else self.put)

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
        return replace(self, spread=clean) if mode in ("spread", "both") else replace(self, put=clean)

    def to_dict(self) -> dict:
        return {"name": self.name, "instruments": list(self.instruments), "put": self.put, "spread": self.spread, "management": self.management}


def validate_profile(p: Profile) -> Profile:
    name = p.name.strip()
    if not name:
        raise ValueError("el perfil necesita un nombre")
    instruments = tuple(i for i in INSTRUMENTS if i in p.instruments)
    if not instruments:
        raise ValueError(f"«{name}»: activa al menos un instrumento (put o spread)")
    if p.management not in ("tastytrade", "tpc"):
        raise ValueError(f"«{name}»: gestión no válida")
    return replace(p, name=name, instruments=instruments)


def validate_profiles(profiles: list[Profile], default: Optional[str]) -> tuple[list[Profile], str]:
    out = [validate_profile(p) for p in profiles]
    if not out:
        raise ValueError("tiene que quedar al menos un perfil")
    names = [p.name for p in out]
    if len(set(n.casefold() for n in names)) != len(names):
        raise ValueError("dos perfiles no pueden llamarse igual")
    return out, (default if default in names else names[0])


def dump(profiles: list[Profile], default: str) -> str:
    return json.dumps({"profiles": [p.to_dict() for p in profiles], "default": default})


def load(raw: str) -> Optional[tuple[list[Profile], str]]:
    """(perfiles, perfil inicial) de un JSON guardado, o None si no hay o ya no es válido."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        profiles = [Profile(d["name"], tuple(d["instruments"]), {str(k): str(v) for k, v in d["put"].items()},
                            {str(k): str(v) for k, v in d["spread"].items()}, d.get("management", "tastytrade")) for d in data["profiles"]]
        return validate_profiles(profiles, data.get("default"))
    except Exception:
        return None
