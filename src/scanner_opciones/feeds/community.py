"""Fuente de contratos de la comunidad de Tortuga Trades: las aperturas (`ideas`) del JSON que deja la rutina diaria."""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from scanner_opciones.community.data import CommunityData, CommunityError, load_community
from scanner_opciones.feeds.base import ContractIdea

log = logging.getLogger(__name__)


class CommunityFeed:
    """Lee el JSON de la comunidad solo si el fichero cambió; si el nuevo es inválido conserva el último bueno (y avisa una vez en el log)."""

    def __init__(self, path: Callable[[], str | Path], name: str = "Comunidad"):
        self.name = name
        self._path = path
        self._cached: Optional[tuple[float, CommunityData]] = None      # (mtime del JSON, datos)
        self._error: Optional[str] = None

    def data(self) -> Optional[CommunityData]:
        """Datos completos del JSON (tickers del screener e ideas). None si el fichero no existe."""
        path = Path(self._path())
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._cached = None
            return None
        if self._cached is not None and self._cached[0] == mtime:
            return self._cached[1]
        try:
            data = load_community(path)
        except CommunityError as exc:
            if self._error != str(exc):
                log.warning("Comunidad: %s no es válido (%s); se conservan los últimos datos buenos", path, exc)
                self._error = str(exc)
            return self._cached[1] if self._cached is not None else None
        self._cached, self._error = (mtime, data), None
        for w in data.warnings:
            log.warning("Comunidad: %s", w)
        return data

    def contracts(self) -> list[ContractIdea]:
        data = self.data()
        return [replace(i, source=self.name) for i in data.ideas] if data is not None else []

    def tickers(self) -> list[str]:
        return sorted({i.ticker for i in self.contracts()})

    def stamp(self) -> Optional[str]:
        data = self.data()
        return data.generated_at.isoformat() if data is not None else None
