"""Simulación de cartera si se venden y son asignados los contratos seleccionados (RF-14).

Se asume asignación del 100 % de los contratos seleccionados al precio de strike (Q-14).
El margen es una APROXIMACIÓN: suma de los what-if individuales (Q-13).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from scanner_opciones.config.settings import CushionThresholds
from scanner_opciones.domain.models import (
    AccountSummary, OptionContract, Position, RiskMetric, SectorExposure,
)
from scanner_opciones.portfolio.cushion import classify
from scanner_opciones.portfolio.leverage import AssignmentExposure, assignment_exposure
from scanner_opciones.portfolio.diversification import (
    NO_SECTOR, WeekExposure, sector_exposure, weekly_sector_exposure,
)


@dataclass(frozen=True)
class SimulatedTrade:
    contract: OptionContract
    quantity: int                      # nº de contratos vendidos (> 0)
    sector: Optional[str] = None
    initial_margin: Optional[float] = None   # margen what-if para `quantity` contratos


@dataclass(frozen=True)
class SimulationResult:
    before: SectorExposure
    after: SectorExposure
    delta_pct: dict[str, float] = field(default_factory=dict)   # puntos porcentuales por sector
    added_nominal: float = 0.0
    added_margin: float = 0.0
    margin_complete: bool = True       # False si algún contrato no tenía margen calculado
    margin_is_approximate: bool = True
    cushion_before: Optional[RiskMetric] = None
    cushion_after: Optional[RiskMetric] = None
    weeks_before: list[WeekExposure] = field(default_factory=list)
    weeks_after: list[WeekExposure] = field(default_factory=list)
    assignment_before: Optional[AssignmentExposure] = None
    assignment_after: Optional[AssignmentExposure] = None


def simulate(
    positions: list[Position],
    trades: list[SimulatedTrade],
    account: Optional[AccountSummary],
    thresholds: CushionThresholds,
    today: date,
    weeks: int = 5,
) -> SimulationResult:
    for t in trades:
        if t.quantity <= 0:
            raise ValueError("La cantidad de cada contrato simulado debe ser > 0")

    hypothetical = [
        Position(
            ticker=t.contract.ticker, quantity=-t.quantity, market_value=0.0,
            sector=t.sector or NO_SECTOR, option=t.contract,
        )
        for t in trades
    ]
    after_positions = list(positions) + hypothetical
    before = sector_exposure(positions)
    after = sector_exposure(after_positions)

    sectors = set(before.weights_pct) | set(after.weights_pct)
    delta = {s: after.weights_pct.get(s, 0.0) - before.weights_pct.get(s, 0.0) for s in sectors}

    added_margin = sum(t.initial_margin for t in trades if t.initial_margin is not None)
    complete = all(t.initial_margin is not None for t in trades)

    cb = ca = None
    if account is not None:
        nl = account.net_liquidation
        before_c = account.cushion_pct  # el de IBKR
        cb = RiskMetric(before_c, account.excess_liquidity, classify(before_c, thresholds))
        if before_c is not None and account.excess_liquidity is not None and nl and nl > 0:
            excess_after = account.excess_liquidity - added_margin
            after_c = before_c - added_margin / nl * 100  # aproximación: el cushion baja lo que sube el margen
            ca = RiskMetric(after_c, excess_after, classify(after_c, thresholds))
        else:
            ca = RiskMetric(None, None, classify(None, thresholds))

    return SimulationResult(
        before=before, after=after, delta_pct=delta,
        added_nominal=after.total_exposure - before.total_exposure,
        added_margin=added_margin, margin_complete=complete,
        cushion_before=cb, cushion_after=ca,
        weeks_before=weekly_sector_exposure(positions, today, weeks),
        weeks_after=weekly_sector_exposure(after_positions, today, weeks),
        assignment_before=assignment_exposure(positions, account),
        assignment_after=assignment_exposure(after_positions, account),
    )
