"""Puerto `CompanyNameProvider`: nombre de la empresa de un proveedor externo (tastytrade). Sirve para completar las
fuentes que no lo traen (los tickers escritos a mano). Nada fuera de las implementaciones reales debe importar el SDK."""
from __future__ import annotations

from typing import Protocol


class CompanyNameProvider(Protocol):
    async def get_company_names(self, tickers: list[str]) -> dict[str, str]:
        """{ticker: nombre}. Los que el proveedor no conoce no aparecen. Lanza `VolatilityError` si falla."""
        ...
