"""Selección de contratos a GUARDAR a partir de la cadena (rango configurable)."""
from __future__ import annotations

from datetime import date

from scanner_opciones.config.settings import CandidateRange
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionChain, OptionContract
from scanner_opciones.metrics.yields import strike_distance_pct

_EPS = 1e-9


def candidate_contracts(
    chain: OptionChain, underlying_price: float, today: date, rng: CandidateRange
) -> list[OptionContract]:
    """Puts con DTE en [dte_min, dte_max] y strike entre `strike_below_pct_min` y `_max` por debajo."""
    if underlying_price is None or underlying_price <= 0:
        return []
    out: list[OptionContract] = []
    for expiry in sorted(chain.expiries):
        dte = (expiry - today).days
        if not (rng.dte_min <= dte <= rng.dte_max):
            continue
        for strike in sorted(chain.strikes):
            dist = strike_distance_pct(underlying_price, strike)
            if dist is None:
                continue
            if rng.strike_below_pct_min - _EPS <= dist <= rng.strike_below_pct_max + _EPS:
                out.append(OptionContract(chain.ticker, expiry, strike, OptionRight.PUT, chain.multiplier))
    return out
