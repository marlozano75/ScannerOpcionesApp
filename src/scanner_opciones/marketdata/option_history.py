"""Puerto `OptionHistoryProvider`: cierre diario del BID de contratos concretos (también ya vencidos), para medir cuánta prima daba de verdad un ticker.

Lo implementa `TastytradeVolatility` (velas de DXLink con `price=bid`). El resultado solo trae los símbolos que RESPONDIERON: uno sin ninguna vela con bid
(sin mercado) vuelve con la serie vacía y uno que no respondió no aparece, para poder pedirlo otra vez."""
from __future__ import annotations

from datetime import date
from typing import Mapping, Protocol, Sequence


class OptionHistoryProvider(Protocol):
    async def option_bid_history(self, symbols: Sequence[str], days: int) -> Mapping[str, Mapping[date, float]]:
        """{símbolo dxFeed de la opción (`.AAPL261016P250`): {día: último bid del día (> 0)}} de los últimos `days` días."""
        ...
