"""Predicados de filtrado. `reject_reason` devuelve el motivo de descarte o None si pasa."""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.metrics.yields import gross_yield_ref_pct, reference_price, strike_distance_pct
from scanner_opciones.rankedstocks.loader import clean_ticker
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.currency import usd_rate

_EPS = 1e-9  # tolerancia para comparaciones en el límite (float)


def iv_hv_available(info: Optional[TickerInfo]) -> bool:
    return info is not None and bool(info.iv30) and bool(info.hv30)


def iv_change_available(info: Optional[TickerInfo]) -> bool:
    return info is not None and info.iv_change_5d is not None


def iv_hv_reason(info: Optional[TickerInfo], criteria: ScanCriteria) -> Optional[str]:
    """Motivo por el que IV30 / HV30 no llega al mínimo, o None (también si el filtro no está activo).
    Sin IV30 o HV30 el filtro no se puede evaluar y NO descarta (ni siquiera con los respaldos de `AppService.infos`)."""
    if criteria.min_iv_hv_ratio is None or not iv_hv_available(info):
        return None
    ratio = info.iv30 / info.hv30
    if ratio < criteria.min_iv_hv_ratio - _EPS:
        return f"IV/HV {ratio:.2f} < {criteria.min_iv_hv_ratio:g}"
    return None


def iv_change_reason(info: Optional[TickerInfo], criteria: ScanCriteria) -> Optional[str]:
    """Motivo por el que la IV ha subido más de lo permitido en 5 días, o None. Sin el dato no se puede evaluar y no descarta."""
    if criteria.max_iv_change_5d is None or not iv_change_available(info):
        return None
    if info.iv_change_5d > criteria.max_iv_change_5d + _EPS:
        return f"la IV ha subido {info.iv_change_5d:+.2f} puntos en 5 días (> {criteria.max_iv_change_5d:g})"
    return None


def reject_reason(
    snap: ContractSnapshot,
    underlying_price: Optional[float],
    today: date,
    criteria: ScanCriteria,
    iv_rank: Optional[float] = None,
    iv_percentile: Optional[float] = None,
    info: Optional[TickerInfo] = None,
    atr: Optional[float] = None,
    nlv_usd: Optional[float] = None,
) -> Optional[str]:
    c = snap.contract
    if c.right is not OptionRight.PUT:
        return "no es una put"
    if criteria.source_tickers is not None and clean_ticker(c.ticker) not in criteria.source_tickers:
        return "no está en ninguna de las fuentes elegidas"
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
    if criteria.min_bid is not None and snap.bid is not None and 0 <= snap.bid < criteria.min_bid - _EPS:
        return f"bid {snap.bid:g} < mínimo {criteria.min_bid:g}"      # sin bid no se vende (y el ask, a menudo irreal, inflaría el yield)
    y = gross_yield_ref_pct(snap.bid, snap.ask, c.strike, criteria.price_reference, criteria.price_spread_pct)
    if y is None:
        return "sin yield (cotización no válida)"
    if y < criteria.min_yield_pct - _EPS:        # yield del periodo (prima / strike hasta el vencimiento), no anualizado
        return f"yield del periodo {y:.2f}% < {criteria.min_yield_pct}%"
    if criteria.min_put_credit is not None:      # prima por contrato con el precio de venta de referencia (las de céntimos se comen las comisiones)
        credit = (reference_price(snap.bid, snap.ask, criteria.price_reference, criteria.price_spread_pct) or 0.0) * c.multiplier * usd_rate(nlv_usd, c.ticker)
        if credit < criteria.min_put_credit - _EPS:
            return f"crédito {credit:.0f} $ < mínimo {criteria.min_put_credit:g} $"
    if criteria.max_capital_risk_pct is not None and nlv_usd:     # capital en riesgo: lo que se pierde si el subyacente llega a 0 (strike − prima)
        premium = reference_price(snap.bid, snap.ask, criteria.price_reference, criteria.price_spread_pct) or 0.0
        risk = (c.strike - premium) * c.multiplier * usd_rate(nlv_usd, c.ticker) / nlv_usd * 100
        if risk > criteria.max_capital_risk_pct + _EPS:
            return f"capital en riesgo {risk:.1f}% del NLV > máximo {criteria.max_capital_risk_pct:g}%"
    if criteria.min_oi is not None and (snap.open_interest is None or snap.open_interest < criteria.min_oi):
        return "OI insuficiente o desconocido"
    if criteria.min_bid_size is not None and (snap.bid_size is None or snap.bid_size < criteria.min_bid_size):
        return "Bid size insuficiente o desconocido"
    if criteria.max_spread_pct is not None and (
        snap.spread_pct is None or snap.spread_pct > criteria.max_spread_pct + _EPS
    ):
        return "spread demasiado alto o desconocido"
    # un dato que falta no se puede evaluar y no descarta (IV Rank, IV Percentile, IV/HV, cambio de IV y ATR)
    rank_fails = criteria.min_iv_rank is not None and iv_rank is not None and iv_rank < criteria.min_iv_rank
    pct_fails = criteria.min_iv_percentile is not None and iv_percentile is not None and iv_percentile < criteria.min_iv_percentile
    if criteria.iv_any and criteria.min_iv_rank is not None and criteria.min_iv_percentile is not None:
        # basta con uno: se descarta solo si los dos se miden y los dos fallan (un dato que falta no se puede evaluar)
        if rank_fails and pct_fails:
            return "IV Rank e IV Percentile insuficientes"
    else:
        if rank_fails:
            return "IV Rank insuficiente"
        if pct_fails:
            return "IV Percentile insuficiente"
    if (why := iv_hv_reason(info, criteria)) is not None:      # IV/HV y cambio de IV son obligatorios (no cuentan como señales de entrada)
        return why
    if (why := iv_change_reason(info, criteria)) is not None:
        return why
    if criteria.min_abs_delta is not None or criteria.max_abs_delta is not None:
        if snap.delta is None:
            return "delta desconocida"
        d = abs(snap.delta)
        if criteria.min_abs_delta is not None and d < criteria.min_abs_delta - _EPS:
            return f"|delta| {d:.2f} < {criteria.min_abs_delta:g}"
        if criteria.max_abs_delta is not None and d > criteria.max_abs_delta + _EPS:
            return f"|delta| {d:.2f} > {criteria.max_abs_delta:g}"
    if criteria.min_atr_multiple is not None and atr is not None:
        if underlying_price - c.strike < criteria.min_atr_multiple * atr - _EPS:
            return f"strike a {underlying_price - c.strike:.2f} del precio, menos de {criteria.min_atr_multiple:g} ATR ({atr:.2f})"
    if criteria.expected_move_mult is not None:
        # IV del vencimiento (tastytrade, la del movimiento esperado de su plataforma); sin ella, la del contrato
        iv = ((info.expiry_ivs or {}).get(c.expiry.isoformat()) if info is not None else None) or snap.iv
        if iv is None or iv <= 0:
            return "sin IV para calcular el movimiento esperado"
        limit = underlying_price - criteria.expected_move_mult * underlying_price * iv * math.sqrt(dte / 365)
        if c.strike > limit + _EPS:
            return f"strike {c.strike:g} dentro del movimiento esperado (límite {limit:.2f})"
    return None

