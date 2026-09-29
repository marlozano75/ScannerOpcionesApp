"""Exposición nominal de asignación y apalancamiento por asignación.

- Short Put Exposure  = suma de strike * multiplicador * nº contratos de las puts vendidas.
- Long Put Protection = suma de lo mismo para las puts compradas.
- Nominal Assignment Exposure (NAE) = Short Put Exposure - Long Put Protection.
- Leverage Assignment = NAE / Net Liquidation Value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, Position
from scanner_opciones.portfolio.diversification import nominal


def long_put_protection(positions: Iterable[Position]) -> float:
    return sum(
        p.option.strike * p.option.multiplier * p.quantity
        for p in positions
        if p.option is not None and p.option.right is OptionRight.PUT and p.quantity > 0
    )


def short_put_exposure(positions: Iterable[Position]) -> float:
    return sum(nominal(p) for p in positions)


@dataclass(frozen=True)
class AssignmentExposure:
    gross_position_value: Optional[float]
    short_put_exposure: float
    long_put_protection: float
    nominal_assignment_exposure: float
    net_liquidation: Optional[float]
    leverage_assignment: Optional[float]  # NAE / NLV (veces); None si no hay NLV válido


def leverage_assignment(nae: float, net_liquidation: Optional[float]) -> Optional[float]:
    if net_liquidation is None or net_liquidation <= 0:
        return None
    return nae / net_liquidation


def assignment_exposure(positions: list[Position], account: Optional[AccountSummary]) -> AssignmentExposure:
    short = short_put_exposure(positions)
    long_ = long_put_protection(positions)
    nae = short - long_
    nlv = account.net_liquidation if account else None
    return AssignmentExposure(
        gross_position_value=account.gross_position_value if account else None,
        short_put_exposure=short,
        long_put_protection=long_,
        nominal_assignment_exposure=nae,
        net_liquidation=nlv,
        leverage_assignment=leverage_assignment(nae, nlv),
    )
