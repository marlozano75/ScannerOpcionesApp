"""Motor de escaneo: filtra snapshots y los enriquece con el impacto en la cartera (RF-11..13)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.models import ContractSnapshot, Position, TickerInfo
from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.metrics.yields import (
    annualized_yield_pct, gross_yield_ref_pct, reference_price, strike_distance_pct,
)
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
    reference_price: Optional[float] = None       # precio de venta usado
    yield_ref_pct: Optional[float] = None         # yield con el precio de referencia
    yield_ref_annualized_pct: Optional[float] = None
    yield_bid_pct: Optional[float] = None         # yield vendiendo al bid (para comparar)


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
        dte = c.dte(today)
        ref = reference_price(snap.bid, snap.ask, criteria.price_reference, criteria.price_spread_pct)
        y_ref = gross_yield_ref_pct(snap.bid, snap.ask, c.strike, criteria.price_reference, criteria.price_spread_pct)
        results.append(ScanResult(
            snap, info, dte, strike_distance_pct(price, c.strike), impact,
            reference_price=ref, yield_ref_pct=y_ref,
            yield_ref_annualized_pct=annualized_yield_pct(y_ref, dte),
            yield_bid_pct=gross_yield_ref_pct(snap.bid, snap.ask, c.strike, PriceReference.BID),
        ))
    results.sort(key=lambda r: r.yield_ref_annualized_pct or 0.0, reverse=True)
    return ScanOutput(results, rejected, rejections)
