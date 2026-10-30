"""Marcas de los resultados que se salen de los límites del perfil guardado.

Si el usuario relaja o desactiva un filtro respecto al perfil cargado, el Scanner devuelve filas que el perfil habría descartado. Aquí se
mide, celda a celda, cuánto se pasa cada valor del límite guardado: `warn` si se queda a menos de `SMALL` (25 %) del límite y `bad` si se
pasa más. Funciones puras sobre `ScanCriteria` (el del perfil) y los resultados del escaneo."""
from __future__ import annotations

from typing import Optional

from scanner_opciones.scanner.currency import usd_rate

SMALL = 0.25      # fracción del límite hasta la que el incumplimiento es «pequeño»


def _sev(value: Optional[float], limit: Optional[float], *, minimum: bool) -> Optional[str]:
    """None si cumple (o no hay dato/límite); `warn` o `bad` si incumple el mínimo (`minimum`) o el máximo `limit`."""
    if value is None or limit is None:
        return None
    gap = (limit - value) if minimum else (value - limit)
    if gap <= 1e-9:
        return None
    return "warn" if gap <= SMALL * max(abs(limit), 1e-9) else "bad"


def _put(marks: dict, col: str, sev: Optional[str]) -> None:
    if sev:
        marks[col] = sev


def _short_marks(r, c, kind: str, nlv, nlv_usd_rate: float) -> dict[str, str]:
    """Columnas de la pata vendida (comunes a la put sola y al spread). `kind`: put | spread; `c` = criterios del perfil de ese juego."""
    s, i = r.snapshot, r.info
    m: dict[str, str] = {}
    _put(m, "desc", _sev(r.strike_distance_pct, c.strike_below_pct_min, minimum=True))
    _put(m, "dte", _sev(float(r.dte), float(c.dte_min), minimum=True) or _sev(float(r.dte), float(c.dte_max), minimum=False))
    if s.delta is not None:
        _put(m, "delta", _sev(abs(s.delta), c.min_abs_delta, minimum=True) or _sev(abs(s.delta), c.max_abs_delta, minimum=False))
    iv_rank, iv_pct = _sev(s.iv_rank, c.min_iv_rank, minimum=True), _sev(s.iv_percentile, c.min_iv_percentile, minimum=True)
    if c.iv_any and c.min_iv_rank is not None and c.min_iv_percentile is not None:     # basta con que cumpla uno
        if not (iv_rank is None or iv_pct is None):
            iv_rank = iv_pct = "bad" if "bad" in (iv_rank, iv_pct) and iv_rank == iv_pct else "warn"
        else:
            iv_rank = iv_pct = None
    _put(m, "ivr", iv_rank)
    _put(m, "ivp", iv_pct)
    _put(m, "spread", _sev(s.spread_pct, c.max_spread_pct, minimum=False))
    _put(m, "bidsize", _sev(None if s.bid_size is None else float(s.bid_size), None if c.min_bid_size is None else float(c.min_bid_size), minimum=True))
    _put(m, "oi", _sev(None if s.open_interest is None else float(s.open_interest), None if c.min_oi is None else float(c.min_oi), minimum=True))
    if i is not None:
        if c.min_option_liquidity is not None and i.option_liquidity is not None:
            _put(m, "liq", _sev(float(i.option_liquidity), float(c.min_option_liquidity), minimum=True))
        px = i.underlying_price
        _put(m, "price", _sev(px, c.min_price, minimum=True) or _sev(px, c.max_price, minimum=False))
        if c.avoid_earnings and i.next_earnings and s.updated_at.date() <= i.next_earnings <= s.contract.expiry:
            m["earn"] = "bad"
    return m


def row_marks(r, kind: str, c, spread=None, nlv=None) -> dict[str, str]:
    """Marcas de una fila: `r` ScanResult de la pata vendida, `kind` put|spread, `c` los criterios guardados en el perfil para ese
    instrumento, `spread` el SpreadResult si es un spread, `nlv` el NLV en USD (con `.rates`) para los límites en % del NLV."""
    contract = r.snapshot.contract
    rate = usd_rate(nlv, contract.ticker) if nlv else 1.0
    m = _short_marks(r, c, kind, nlv, rate)
    if kind == "spread" and spread is not None and spread.discount_pct is not None:      # el descuento de un spread llega al breakeven con su crédito neto
        m.pop("desc", None)
        _put(m, "desc", _sev(spread.discount_pct, c.strike_below_pct_min, minimum=True))
    if kind == "put":
        if r.net_price is not None:
            credit = r.net_price * contract.multiplier * rate
            _put(m, "yp", _sev(r.yield_ref_pct, c.min_yield_pct, minimum=True))
            _put(m, "credit", _sev(credit, c.min_put_credit, minimum=True))
            _put(m, "scredit", m.get("credit"))
            if nlv:
                risk = (contract.strike - r.net_price) * contract.multiplier * rate / nlv * 100
                _put(m, "risk", _sev(risk, c.max_capital_risk_pct, minimum=False))
                _put(m, "lossnlv", m.get("risk"))
        return m
    if spread is None:
        return m
    credit = spread.credit * contract.multiplier * rate
    _put(m, "scredit", _sev(credit, c.min_spread_credit, minimum=True))
    _put(m, "cw", _sev(spread.credit_width_pct, c.min_credit_width_pct, minimum=True)
        or _sev(spread.credit_width_pct, c.max_credit_width_pct, minimum=False))
    _put(m, "rr", _sev(spread.return_on_risk_pct, c.min_return_on_risk_pct, minimum=True))
    if nlv:
        _put(m, "lossnlv", _sev(spread.max_loss * rate / nlv * 100, c.max_loss_nlv_pct, minimum=False))
    lg = spread.long
    if lg.delta is not None:
        _put(m, "ldelta", _sev(abs(lg.delta), c.max_long_abs_delta, minimum=False))
    _put(m, "lspread", _sev(lg.spread_pct, c.max_long_spread_pct, minimum=False))
    _put(m, "loi", _sev(None if lg.open_interest is None else float(lg.open_interest), None if c.min_long_oi is None else float(c.min_long_oi), minimum=True))
    return m


def row_key(r, spread=None) -> str:
    c = r.snapshot.contract
    return f"{c.ticker}|{c.expiry}|{c.strike}" + (f"|{spread.long.contract.strike}" if spread else "")
