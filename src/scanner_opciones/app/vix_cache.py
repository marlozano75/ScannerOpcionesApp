"""Último VIX leído (con su histórico y la curva de futuros), guardado en `meta` para no volver a pedirlo a tastytrade en cada arranque con
el mercado cerrado: si lo guardado ya es posterior al último cierre no puede haber cambiado (`AppService._vix_is_current`)."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from scanner_opciones.domain.models import VixData


def dump(vix: VixData) -> str:
    return json.dumps({
        "current": vix.current,
        "last_closes": [[d.isoformat(), v] for d, v in vix.last_closes],
        "futures": [[d.isoformat(), v] for d, v in vix.futures],
        "updated_at": vix.updated_at.isoformat() if vix.updated_at else None,
    })


def load(raw: Optional[str]) -> Optional[VixData]:
    """El VIX guardado, o None si no hay o ya no se puede leer."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return VixData(
            current=None if data.get("current") is None else float(data["current"]),
            last_closes=[(date.fromisoformat(d), float(v)) for d, v in data.get("last_closes", [])],
            futures=[(date.fromisoformat(d), float(v)) for d, v in data.get("futures", [])],
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
        )
    except (ValueError, TypeError, KeyError):
        return None
