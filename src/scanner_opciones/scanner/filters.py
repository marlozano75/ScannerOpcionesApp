"""Predicados de filtrado. `reject_reason` devuelve el motivo de descarte o None si pasa."""
from __future__ import annotations

from datetime import date
from typing import Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot
from scanner_opciones.metrics.yields import annualized_yield_pct, gross_yield_ref_pct, strike_distance_pct
from scanner_opciones.scanner.criteria import ScanCriteria

_EPS = 1e-9  # tolerancia para comparaciones en el límite (float)


def reject_reason(
    snap: ContractSnapshot,
    underlying_price: Optional[float],
    today: date,
    criteria: ScanCriteria,
    iv_rank: Optional[float] = None,
    iv_percentile: Optional[float] = None,
) -> Optional[str]:
    c = snap.contract
    if c.right is not OptionRight.PUT:
        return "no es una put"
    dte = c.dte(today)
    if not (criteria.dte_min <= dte <= criteria.dte_max):
        return f"DTE {dte} fuera de [{criteria.dte_min}, {criteria.dte_max}]"
    dist = strike_distance_pct(underlying_price, c.strike)
    if dist is None:
        return "sin precio del subyacente"
    if not (criteria.strike_below_pct_min - _EPS <= dist <= criteria.strike_below_pct_max + _EPS):
        return f"strike a {dist:.2f}% fuera de [{criteria.strike_below_pct_min}, {criteria.strike_below_pct_max}]"
    y = gross_yield_ref_pct(snap.bid, snap.ask, c.strike, criteria.price_reference, criteria.price_spread_pct)
    if y is None:
        return "sin yield (cotización no válida)"
    annual = annualized_yield_pct(y, dte)
    if annual is None or annual < criteria.min_annual_yield_pct - _EPS:
        return f"yield anual {annual or 0:.2f}% < {criteria.min_annual_yield_pct}%"
    if criteria.min_oi is not None and (snap.open_interest is None or snap.open_interest < criteria.min_oi):
        return "OI insuficiente o desconocido"
    if criteria.min_bid_size is not None and (snap.bid_size is None or snap.bid_size < criteria.min_bid_size):
        return "Bid size insuficiente o desconocido"
    if criteria.max_spread_pct is not None and (
        snap.spread_pct is None or snap.spread_pct > criteria.max_spread_pct + _EPS
    ):
        return "spread demasiado alto o desconocido"
    if criteria.min_iv_rank is not None and (iv_rank is None or iv_rank < criteria.min_iv_rank):
        return "IV Rank insuficiente o desconocido"
    if criteria.min_iv_percentile is not None and (
        iv_percentile is None or iv_percentile < criteria.min_iv_percentile
    ):
        return "IV Percentile insuficiente o desconocido"
    return None
