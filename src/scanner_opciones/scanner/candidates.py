"""Selección de contratos a GUARDAR a partir de la cadena (rango configurable)."""
from __future__ import annotations

from datetime import date

from scanner_opciones.config.settings import CandidateRange, CdsSettings
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


def is_monthly_expiry(expiry: date) -> bool:
    """Vencimiento mensual estándar: el tercer viernes del mes (los semanales y de fin de mes tienen menos liquidez)."""
    return expiry.weekday() == 4 and 15 <= expiry.day <= 21


def on_strike_grid(strike: float, multiple: float) -> bool:
    """¿Cae el strike en la rejilla de `multiple` $? Por debajo de 25 $ vale cualquiera; `multiple` 0 = sin rejilla."""
    if multiple <= 0 or strike < 25:
        return True
    ratio = strike / multiple
    return abs(ratio - round(ratio)) < 1e-6


def call_candidate_contracts(chain: OptionChain, underlying_price: float, today: date, cds: CdsSettings) -> list[OptionContract]:
    """Calls para los Call Debit Spread: DTE en [dte_min, dte_max] y strike entre `long_otm_pct_min` y `long_otm_pct_max` por ENCIMA del precio,
    más las calls `width_options` $ por encima de cada una que existan en la cadena (patas vendidas)."""
    if underlying_price is None or underlying_price <= 0:
        return []
    known = {round(k, 4) for k in chain.strikes}
    out: dict[tuple, OptionContract] = {}
    for expiry in sorted(chain.expiries):
        if not (cds.dte_min <= (expiry - today).days <= cds.dte_max):
            continue
        if cds.monthly_only and not is_monthly_expiry(expiry):
            continue
        for strike in sorted(chain.strikes):
            otm = (strike / underlying_price - 1) * 100
            if not (cds.long_otm_pct_min - _EPS <= otm <= cds.long_otm_pct_max + _EPS):
                continue
            for k in (strike, *(round(strike + w, 4) for w in cds.width_options)):
                if round(k, 4) in known and on_strike_grid(k, cds.strike_multiple):
                    out[(expiry, round(k, 4))] = OptionContract(chain.ticker, expiry, k, OptionRight.CALL, chain.multiplier)
    return list(out.values())
