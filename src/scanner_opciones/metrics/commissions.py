"""Comisiones de IBKR de las opciones de EE. UU. (tarifa fija de IBKR Pro).

Medido el 2026-10-30 con las 60 últimas ejecuciones de opciones de la cuenta (Tortuga) y con órdenes what-if: una orden de 1 contrato paga
≈ 1,05 $ (mínimo de 1,00 $ por orden + ≈ 0,05 $ de tasas); 2 contratos ≈ 1,36 $ (2 × 0,65 + tasas); un spread enviado como UNA orden combo ≈ 1,10 $
(un solo mínimo repartido entre las dos patas + tasas de cada una). El what-if de IBKR no sirve para esto: con tarifa escalonada devuelve `commission`
vacío y un rango mínimo–máximo muy ancho (0,56–1,55 $ para 1 contrato)."""
from __future__ import annotations

from dataclasses import dataclass

US_MULTIPLIER = 100       # solo las opciones de acciones de EE. UU. (los índices y valores europeos tienen otra tarifa y no se tocan)


@dataclass(frozen=True)
class CommissionModel:
    per_contract: float = 0.65        # comisión por contrato
    min_per_order: float = 1.0        # mínimo por orden (un spread combo paga un solo mínimo)
    fees_per_contract: float = 0.05   # tasas regulatorias y de cámara por contrato y pata (estimación)
    contracts: int = 1                # contratos por orden con los que se amortiza el mínimo

    def order_cost(self, legs: int = 1) -> float:
        """Coste total en $ de UNA orden de apertura (o de cierre) de `contracts` contratos con `legs` patas."""
        return max(self.min_per_order, self.per_contract * self.contracts) + self.fees_per_contract * self.contracts * legs

    def per_share(self, multiplier: float, legs: int = 1, orders: int = 1) -> float:
        """Coste por acción (por unidad del subyacente) de `orders` órdenes iguales: se resta de la prima para obtener la prima neta."""
        if multiplier != US_MULTIPLIER:
            return 0.0
        return self.order_cost(legs) * orders / (multiplier * self.contracts)
