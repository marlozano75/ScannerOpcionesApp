"""Puerto `WatchlistProvider`: listas públicas de un proveedor externo (tastytrade). Dan un conjunto de tickers actualizado
donde poner el foco. Nada fuera de las implementaciones reales debe importar el SDK."""
from __future__ import annotations

from typing import Protocol


class WatchlistProvider(Protocol):
    async def get_public_watchlist(self, name: str) -> list[str]:
        """Tickers (acciones y ETF, con el formato de la app: «BF-B») de la lista pública `name`, sin duplicados y en su orden.
        Lanza `VolatilityError` si falla o la lista no existe."""
        ...
