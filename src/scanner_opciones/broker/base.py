"""Puerto del broker. El dominio y los jobs solo dependen de esta interfaz."""
from __future__ import annotations

from datetime import date
from typing import Optional, Protocol, Sequence

from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, VixData,
)


class BrokerGateway(Protocol):
    """Todos los métodos son async. Errores: BrokerError y subclases."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def get_account_summary(self) -> AccountSummary: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        """(sector, categoría) según IBKR."""

    async def get_underlying_price(self, ticker: str) -> Optional[float]: ...

    async def get_underlying_prices(self, tickers: Sequence[str]) -> dict[str, float]:
        """Precio actual de varios subyacentes de una vez. Los que no tengan precio no aparecen."""

    async def get_option_chain(self, ticker: str) -> OptionChain: ...

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        """None si no aplica / no hay dividendo próximo."""

    async def get_iv_history(self, ticker: str, since: Optional[date]) -> list[tuple[date, float]]:
        """IV diaria del subyacente. Si `since` no es None, solo días posteriores a esa fecha."""

    async def qualify_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        """Devuelve solo los contratos que existen en IBKR, con `con_id` informado."""

    async def get_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        """Cotizaciones por lotes; los contratos sin datos no aparecen o traen campos None."""

    async def what_if_margin(self, contract: OptionContract, quantity: int = 1) -> Optional[float]:
        """Cambio de margen inicial al vender `quantity` contratos (what-if; nunca envía orden)."""

    async def get_vix_data(self, history_days: int, futures_ahead: int) -> VixData: ...
