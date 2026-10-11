"""Predicados de filtrado. `reject_reason` devuelve el motivo de descarte o None si pasa."""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
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
    info: Optional[TickerInfo] = None,
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
    if criteria.min_price is not None and underlying_price < criteria.min_price - _EPS:
        return f"precio {underlying_price:g} < mínimo {criteria.min_price:g}"
    if criteria.max_price is not None and underlying_price > criteria.max_price + _EPS:
        return f"precio {underlying_price:g} > máximo {criteria.max_price:g}"
    y = gross_yield_ref_pct(snap.bid, snap.ask, c.strike, criteria.price_reference, criteria.price_spread_pct)
    if y is None:
        return "sin yield (cotización no válida)"
    if criteria.min_bid is not None and (snap.bid is None or snap.bid < criteria.min_bid - _EPS):
        return f"bid {snap.bid if snap.bid is not None else 'desconocido'} < mínimo {criteria.min_bid:g}"
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
    if criteria.min_iv_hv_ratio is not None:
        if info is None or not info.iv30 or not info.hv30:
            return "sin IV30 / HV30 para calcular IV/HV"
        ratio = info.iv30 / info.hv30
        if ratio < criteria.min_iv_hv_ratio - _EPS:
            return f"IV/HV {ratio:.2f} < {criteria.min_iv_hv_ratio:g}"
    if criteria.max_iv_change_5d is not None:
        if info is None or info.iv_change_5d is None:
            return "sin cambio de IV a 5 días"
        if info.iv_change_5d > criteria.max_iv_change_5d + _EPS:
            return f"la IV ha subido {info.iv_change_5d:+.2f} puntos en 5 días (> {criteria.max_iv_change_5d:g})"
    if criteria.min_abs_delta is not None or criteria.max_abs_delta is not None:
        if snap.delta is None:
            return "delta desconocida"
        d = abs(snap.delta)
        if criteria.min_abs_delta is not None and d < criteria.min_abs_delta - _EPS:
            return f"|delta| {d:.2f} < {criteria.min_abs_delta:g}"
        if criteria.max_abs_delta is not None and d > criteria.max_abs_delta + _EPS:
            return f"|delta| {d:.2f} > {criteria.max_abs_delta:g}"
    if criteria.expected_move_mult is not None:
        if snap.iv is None or snap.iv <= 0:
            return "sin IV del contrato para calcular el movimiento esperado"
        limit = underlying_price - criteria.expected_move_mult * underlying_price * snap.iv * math.sqrt(dte / 365)
        if c.strike > limit + _EPS:
            return f"strike {c.strike:g} dentro del movimiento esperado (límite {limit:.2f})"
    return None

