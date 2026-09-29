"""Motor de escaneo: filtra snapshots y los enriquece con el impacto en la cartera (RF-11..13)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.models import ContractSnapshot, Position, TickerInfo
from scanner_opciones.metrics.yields import strike_distance_pct
from scanner_opciones.portfolio.diversification import CandidateImpact, candidate_impact
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.filters import reject_reason


@dataclass(frozen=True)
class ScanResult:
    snapshot: ContractSnapshot
    info: Optional[TickerInfo]
    dte: int
    strike_distance_pct: Optional[float]
    impact: CandidateImpact


@dataclass(frozen=True)
class ScanOutput:
    results: list[ScanResult]
    rejected_count: int
    rejections: dict[str, str]  # clave "TICKER expiry strike" -> motivo (para depuración)


def run_scan(
    snapshots: Sequence[ContractSnapshot],
    infos: dict[str, TickerInfo],
    positions: list[Position],
    criteria: ScanCriteria,
    today: date,
    include_rejections: bool = False,
) -> ScanOutput:
    results: list[ScanResult] = []
    rejections: dict[str, str] = {}
    rejected = 0
    for snap in snapshots:
        c = snap.contract
        info = infos.get(c.ticker)
        price = info.underlying_price if info else None
        why = reject_reason(snap, price, today, criteria, snap.iv_rank, snap.iv_percentile)
        if why is not None:
            rejected += 1
            if include_rejections:
                rejections[f"{c.ticker} {c.expiry} {c.strike}"] = why
            continue
        impact = candidate_impact(
            positions, info.sector if info else None, c.strike, c.multiplier, c.expiry
        )
        results.append(ScanResult(snap, info, c.dte(today), strike_distance_pct(price, c.strike), impact))
    results.sort(key=lambda r: r.snapshot.yield_annualized_pct or 0.0, reverse=True)
    return ScanOutput(results, rejected, rejections)
