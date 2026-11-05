"""Prima y liquidez de cierre por ticker y perfil (funciones puras). Alimenta `ticker_premium_history` (docs/seleccion-tickers.md).

Un contrato es **cerrable** (recompra fácil) si tiene bid, spread bid/ask ≤ `closing.max_spread_pct` y OI ≥ `closing.min_oi`: los TastyPut, TastySpread y CDS
se cierran en días o semanas, así que de nada sirve una prima alta en un contrato que luego no se puede recomprar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Iterable, Optional

from scanner_opciones.config.settings import CdsSettings, ClosingSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot
from scanner_opciones.metrics.yields import annualized_yield_pct, gross_yield_ref_pct, strike_distance_pct
from scanner_opciones.scanner.criteria import ScanCriteria

CDS_PROFILE = "CDS"          # nombre de la fila de las calls del Call Debit Spread


@dataclass(frozen=True)
class PremiumRow:
    ticker: str
    profile: str
    contracts: int                                   # contratos con bid dentro de la ventana del perfil
    closable: int                                    # de ellos, los cerrables
    best_yield_pct: Optional[float] = None           # mejor yield del periodo (ventana del perfil)
    best_closable_yield_pct: Optional[float] = None  # ... entre los cerrables
    best_ann_wide_pct: Optional[float] = None        # mejor yield anualizado con el mismo descuento y DTE 1…`wide_dte_max` (no depende del calendario de vencimientos)
    median_spread_pct: Optional[float] = None
    max_oi: Optional[int] = None


def is_closable(snap: ContractSnapshot, closing: ClosingSettings) -> bool:
    return (snap.bid is not None and snap.bid > 0 and snap.spread_pct is not None and snap.spread_pct <= closing.max_spread_pct
            and snap.open_interest is not None and snap.open_interest >= closing.min_oi)


def _liquidity(window: list[ContractSnapshot], closing: ClosingSettings) -> tuple[int, int, Optional[float], Optional[int]]:
    quoted = [s for s in window if s.bid is not None and s.bid > 0]
    spreads = [s.spread_pct for s in quoted if s.spread_pct is not None]
    ois = [s.open_interest for s in quoted if s.open_interest is not None]
    return (len(quoted), sum(1 for s in quoted if is_closable(s, closing)),
            round(median(spreads), 2) if spreads else None, max(ois) if ois else None)


def summarize_puts(ticker: str, profile: str, snaps: Iterable[ContractSnapshot], price: Optional[float], today: date,
                   criteria: ScanCriteria, closing: ClosingSettings) -> PremiumRow:
    """Mejor prima de las puts del ticker en la ventana del perfil (descuento y DTE) y su liquidez de cierre."""
    window: list[ContractSnapshot] = []
    yields: dict[int, float] = {}
    best_wide: Optional[float] = None
    for s in snaps:
        c = s.contract
        if c.right is not OptionRight.PUT:
            continue
        dist = strike_distance_pct(price, c.strike)
        if dist is None or dist < criteria.strike_below_pct_min - 1e-9:
            continue
        dte = c.dte(today)
        y = gross_yield_ref_pct(s.bid, s.ask, c.strike, criteria.price_reference, criteria.price_spread_pct)
        if 1 <= dte <= closing.wide_dte_max and y is not None:
            ann = annualized_yield_pct(y, dte)
            if ann is not None and (best_wide is None or ann > best_wide):
                best_wide = ann
        if criteria.dte_min <= dte <= criteria.dte_max and dist <= criteria.strike_below_pct_max + 1e-9:
            window.append(s)
            if y is not None:
                yields[id(s)] = y
    contracts, closable, med, max_oi = _liquidity(window, closing)
    best = max(yields.values()) if yields else None
    best_closable = max((yields[id(s)] for s in window if id(s) in yields and is_closable(s, closing)), default=None)
    return PremiumRow(ticker, profile, contracts, closable, best, best_closable, best_wide, med, max_oi)


def summarize_calls(ticker: str, snaps: Iterable[ContractSnapshot], price: Optional[float], today: date,
                    cds: CdsSettings, closing: ClosingSettings) -> PremiumRow:
    """Liquidez de cierre de las calls de la ventana del CDS (compra prima: no hay yield que registrar)."""
    window = []
    for s in snaps:
        c = s.contract
        if c.right is not OptionRight.CALL or not price:
            continue
        otm = (c.strike / price - 1) * 100
        if cds.dte_min <= c.dte(today) <= cds.dte_max and cds.long_otm_pct_min - 1e-9 <= otm <= cds.long_otm_pct_max + 1e-9:
            window.append(s)
    contracts, closable, med, max_oi = _liquidity(window, closing)
    return PremiumRow(ticker, CDS_PROFILE, contracts, closable, None, None, None, med, max_oi)
