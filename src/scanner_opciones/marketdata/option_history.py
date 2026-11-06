"""Puerto `OptionHistoryProvider`: cierre diario del BID de contratos concretos (también ya vencidos), para medir cuánta prima daba de verdad un ticker; y, como
paso opcional, su ask, OI, IV y volumen para aplicar los filtros de liquidez del perfil.

Lo implementa `TastytradeVolatility` (velas de DXLink con `price=bid`, `price=ask` y `price=last`; OI, IV y volumen solo vienen en las de `last`). El resultado solo
trae los símbolos que RESPONDIERON: uno sin ninguna vela (sin mercado) vuelve con la serie vacía y uno que no respondió no aparece, para poder pedirlo otra vez.
El tamaño del bid no existe en las velas: no se puede traer."""
from __future__ import annotations

from datetime import date
from typing import Callable, Mapping, Optional, Protocol, Sequence


class OptionHistoryProvider(Protocol):
    async def option_bid_history(self, symbols: Sequence[str], days: int,
                                 on_progress: Optional[Callable[[int, int, int], None]] = None) -> Mapping[str, Mapping[date, float]]:
        """{símbolo dxFeed de la opción (`.AAPL261016P250`): {día: último bid del día (> 0)}} de los últimos `days` días.
        `on_progress(respondidos, total, pasada)` se llama tras cada lote."""
        ...

    async def option_detail_history(self, symbols: Sequence[str], days: int,
                                    on_progress: Optional[Callable[[int, int, int], None]] = None) -> Mapping[str, Mapping[str, Mapping]]:
        """{símbolo: {"ask": {día: ask (> 0)}, "last": {día: (OI, IV, volumen)}}}; cualquier elemento de la tupla puede ser None. Un proveedor sin este
        método (pruebas, versiones antiguas) deja el paso de ask/OI/IV sin hacer."""
        ...
