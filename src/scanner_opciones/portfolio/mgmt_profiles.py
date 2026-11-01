"""Perfiles de GESTIÓN: no dependen de un escaneo, solo tienen estrategia de salida.

A diferencia de los perfiles del Scanner (`scanner/profiles.py::Profile`, que parten de tickers y filtran contratos), un perfil de gestión
solo dice CÓMO se gestiona en el Panel una posición ya abierta (estilo base `tastytrade`/`tpc` + los cambios de `portfolio/exits.py`). Se asigna a
una entrada nueva por la FUENTE de la que salió su contrato (Comunidad / MarketRadar) o, si no salió de ninguna, al perfil catch-all (MTO), que
sustituye a la antigua etiqueta «Sin perfil» pero sí tiene gestión.

Los tres sembrados: Comunidad (`source` = la fuente de contratos de la comunidad), Radar (la del Market Radar) y MTO (catch-all). Se pueden crear,
renombrar, recolorear y borrar (salvo el catch-all) desde la configuración, y sus salidas se editan en la pantalla Salidas. Funciones puras; el
almacenamiento (JSON en `meta`) lo hace `AppService`."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from scanner_opciones.portfolio import exits as exits_mod

CATCHALL = "MTO"          # nombre del perfil catch-all sembrado (recibe lo que no sale de ninguna fuente); sustituye a «Sin perfil»
COMMUNITY = "Comunidad"
RADAR = "Radar"


@dataclass(frozen=True)
class MgmtProfile:
    name: str
    management: str = "tpc"                        # estilo base de las reglas de salida: tastytrade | tpc (`portfolio/exits.py`)
    color: str = ""                                # color del botón/etiqueta del Panel (#rrggbb; vacío = sin color)
    exits: dict = field(default_factory=dict)      # estrategia de salida: solo lo que cambia respecto al estilo `management`
    source: str = ""                               # nombre de la fuente de contratos que alimenta este perfil («» = solo manual / catch-all)
    catchall: bool = False                         # True en MTO: recibe todo lo que no encaja; no se borra ni renombra

    def exit_rules(self, cfg) -> exits_mod.ExitRules:
        """Estrategia de salida efectiva: la del estilo de gestión con los cambios de este perfil."""
        return exits_mod.resolve(self.management, self.exits, cfg)

    def to_dict(self) -> dict:
        return {"name": self.name, "management": self.management, "color": self.color, "exits": self.exits,
                "source": self.source, "catchall": self.catchall}


def seed(community_source: str, radar_source: str) -> list[MgmtProfile]:
    """Los tres perfiles de gestión por defecto, ligados a las fuentes de contratos. MTO es el catch-all."""
    return [
        MgmtProfile(COMMUNITY, "tpc", "", {}, community_source, False),
        MgmtProfile(RADAR, "tpc", "", {}, radar_source, False),
        MgmtProfile(CATCHALL, "tpc", "", {}, "", True),
    ]


def validate_profile(p: MgmtProfile) -> MgmtProfile:
    name = p.name.strip()
    if not name:
        raise ValueError("el perfil de gestión necesita un nombre")
    if p.color and not re.fullmatch(r"#[0-9a-fA-F]{6}", p.color):
        raise ValueError(f"«{name}»: color no válido")
    if not exits_mod.is_style(p.management):
        raise ValueError(f"«{name}»: gestión no válida")
    return replace(p, name=name)


def validate_profiles(profiles: list[MgmtProfile], scanner_names: frozenset[str] = frozenset()) -> list[MgmtProfile]:
    """Valida la lista: nombres únicos, sin chocar con los perfiles de escaneo, y exactamente un catch-all (que no se puede quitar)."""
    out = [validate_profile(p) for p in profiles]
    if not out:
        raise ValueError("tiene que quedar al menos un perfil de gestión")
    if sum(1 for p in out if p.catchall) != 1:
        raise ValueError("tiene que haber exactamente un perfil catch-all")
    names = [p.name for p in out]
    if len(set(n.casefold() for n in names)) != len(names):
        raise ValueError("dos perfiles de gestión no pueden llamarse igual")
    clash = {n.casefold() for n in scanner_names} & {n.casefold() for n in names}
    if clash:
        raise ValueError("un perfil de gestión no puede llamarse igual que uno de escaneo")
    return out


def dump(profiles: list[MgmtProfile]) -> str:
    return json.dumps({"profiles": [p.to_dict() for p in profiles]})


def parse(raw: str) -> Optional[list[MgmtProfile]]:
    """Perfiles de un JSON guardado tal cual, sin validar; None si no hay o no se lee."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return [MgmtProfile(str(d["name"]), str(d.get("management", "tpc")), str(d.get("color", "")),
                            exits_mod.check_overrides(d.get("exits") or {}), str(d.get("source", "")), bool(d.get("catchall", False)))
                for d in data["profiles"]]
    except Exception:
        return None


def load(raw: str, scanner_names: frozenset[str] = frozenset()) -> Optional[list[MgmtProfile]]:
    """Perfiles de un JSON guardado, o None si no hay o ya no es válido."""
    parsed = parse(raw)
    if parsed is None:
        return None
    try:
        return validate_profiles(parsed, scanner_names)
    except Exception:
        return None
