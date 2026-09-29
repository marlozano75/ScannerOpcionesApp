"""Exposición potencial total (EPT) y diversificación sectorial (RF-12, RF-13, RF-18, RF-19).

EPT = valor de las acciones + exposición nominal de las puts vendidas
(nominal = strike * multiplicador * nº de contratos). Es la base de todos los pesos.
Las semanas son semanas naturales (lunes-domingo).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import Position, SectorExposure

NO_SECTOR = "Sin sector"


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def is_short_put(p: Position) -> bool:
    return p.option is not None and p.option.right is OptionRight.PUT and p.quantity < 0


def nominal(p: Position) -> float:
    """Exposición nominal de una put vendida (0 para el resto de opciones)."""
    if not is_short_put(p):
        return 0.0
    return p.option.strike * p.option.multiplier * abs(p.quantity)


def exposure_value(p: Position) -> float:
    """Contribución de la posición a la EPT."""
    if p.option is None:
        return p.market_value
    return nominal(p)


def _sector(p: Position) -> str:
    return p.sector or NO_SECTOR


def sector_amounts(positions: Iterable[Position]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for p in positions:
        v = exposure_value(p)
        if v:
            out[_sector(p)] += v
    return dict(out)


def total_exposure(positions: Iterable[Position]) -> float:
    return sum(sector_amounts(positions).values())


def sector_exposure(positions: Iterable[Position]) -> SectorExposure:
    amounts = sector_amounts(positions)
    total = sum(amounts.values())
    weights = {s: v / total * 100 for s, v in amounts.items()} if total > 0 else {}
    return SectorExposure(weights_pct=weights, total_exposure=total)


@dataclass(frozen=True)
class WeekExposure:
    week_start: date
    amounts: dict[str, float] = field(default_factory=dict)  # nominal de puts vendidas por sector
    total: float = 0.0
    weights_pct: dict[str, float] = field(default_factory=dict)  # peso del sector dentro de la semana


def weekly_sector_exposure(positions: Iterable[Position], today: date, weeks: int) -> list[WeekExposure]:
    """Nominal de puts vendidas por sector para cada una de las próximas `weeks` semanas
    (la semana en curso cuenta como la primera). Semanas sin contratos salen con total 0."""
    start = week_start(today)
    buckets: list[dict[str, float]] = [defaultdict(float) for _ in range(weeks)]
    for p in positions:
        if not is_short_put(p):
            continue
        idx = (p.option.expiry - start).days // 7
        if 0 <= idx < weeks:
            buckets[idx][_sector(p)] += nominal(p)
    result = []
    for i, b in enumerate(buckets):
        total = sum(b.values())
        weights = {s: v / total * 100 for s, v in b.items()} if total > 0 else {}
        result.append(WeekExposure(start + timedelta(days=7 * i), dict(b), total, weights))
    return result


@dataclass(frozen=True)
class CandidateImpact:
    """Impacto de añadir un contrato candidato (1 o `qty` contratos vendidos)."""
    nominal: float
    sector: str
    assignment_pct_of_portfolio: Optional[float]   # nominal / (EPT + nominal) * 100
    sector_weight_now_pct: float
    sector_weight_increase_pct: Optional[float]    # puntos porcentuales
    week_weight_pct: Optional[float]               # nominal / (nominal semana + nominal) * 100


def candidate_impact(
    positions: list[Position],
    ticker_sector: Optional[str],
    strike: float,
    multiplier: int,
    expiry: date,
    quantity: int = 1,
) -> CandidateImpact:
    sector = ticker_sector or NO_SECTOR
    nom = strike * multiplier * quantity
    amounts = sector_amounts(positions)
    ept = sum(amounts.values())
    sector_now = amounts.get(sector, 0.0)
    weight_now = sector_now / ept * 100 if ept > 0 else 0.0

    denom = ept + nom
    assignment = nom / denom * 100 if denom > 0 else None
    increase = ((sector_now + nom) / denom * 100 - weight_now) if denom > 0 else None

    wk = week_start(expiry)
    week_nominal = sum(nominal(p) for p in positions if is_short_put(p) and week_start(p.option.expiry) == wk)
    week_weight = nom / (week_nominal + nom) * 100 if (week_nominal + nom) > 0 else None
    return CandidateImpact(nom, sector, assignment, weight_now, increase, week_weight)
