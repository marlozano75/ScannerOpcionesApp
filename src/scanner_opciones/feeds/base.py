"""Puerto de las fuentes de contratos y el contrato concreto que traen. Sin dependencias del resto de la aplicación."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ContractIdea:
    """Put vendida o spread de puts concreto que aporta una fuente. El orden de los campos es el del JSON de la comunidad."""
    day: date                               # día en que se abrió (en la comunidad llega al día siguiente)
    trader: Optional[str]                   # quién lo abrió, si la fuente lo dice
    ticker: str
    strategy: str                           # «Short Put» | «Bull Put Spread»
    expiry: date
    short_strike: float
    long_strike: Optional[float]
    contracts: int
    credit: float                           # prima neta por acción (en un spread, vendida − comprada)
    max_loss: Optional[float] = None        # pérdida máxima de toda la posición en dólares, si se conoce
    source: str = ""                        # nombre de la fuente que lo aportó (`ContractFeed.name`)


@runtime_checkable
class ContractFeed(Protocol):
    """Origen de contratos concretos. Para añadir uno basta una clase con estos miembros y registrarla en `AppService.feeds`."""
    name: str

    def contracts(self) -> Sequence[ContractIdea]:
        """Contratos vigentes de la fuente (cada uno con `source` = `name`); vacío si no hay datos."""

    def tickers(self) -> list[str]:
        """Tickers de esos contratos, sin repetir y ordenados (la fuente también sirve de origen de tickers para los perfiles de escaneo)."""

    def stamp(self) -> Optional[str]:
        """Marca de la versión de los datos (cambia cuando la fuente trae contratos nuevos); None si no hay datos."""


def combined_stamp(feeds: Sequence[ContractFeed]) -> Optional[str]:
    """Marca de varias fuentes juntas: la de una sola es la suya tal cual."""
    stamps = [s for s in (f.stamp() for f in feeds) if s]
    return "|".join(stamps) if stamps else None
