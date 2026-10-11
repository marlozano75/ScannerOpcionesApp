"""Modo «Spread» del scanner: a cada put vendida que pasa los filtros se le empareja una put comprada del mismo
ticker y vencimiento `ancho` $ por debajo (bull put spread). Los filtros del escaneo se aplican a la pata vendida;
la comprada solo necesita cotización válida. El crédito se calcula de forma conservadora: la vendida al precio de
referencia elegido y la comprada al ask."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot
from scanner_opciones.metrics.yields import mid_price
from scanner_opciones.portfolio.diversification import candidate_impact
from scanner_opciones.scanner.criteria import ScanCriteria

EPS = 1e-6


@dataclass(frozen=True)
class SpreadResult:
    short: "object"                  # ScanResult de la pata vendida (engine.py; evita el import circular)
    long: ContractSnapshot           # put comprada
    width: float                     # strike vendido − strike comprado ($)
    credit: float                    # crédito neto por acción
    max_loss: float                  # pérdida máxima por spread en $ = (ancho − crédito) × multiplicador
    credit_width_pct: float
    return_on_risk_pct: float        # crédito / (ancho − crédito)
    return_on_risk_annualized_pct: float
    breakeven: float                 # strike vendido − crédito
    breakeven_distance_pct: Optional[float]   # % por debajo del precio actual
    assignment_pct_of_portfolio: Optional[float]   # nominal = ancho


def _long_leg_index(snapshots: Sequence[ContractSnapshot]) -> dict[tuple, ContractSnapshot]:
    return {(s.contract.ticker, s.contract.expiry, round(s.contract.strike, 4)): s
            for s in snapshots if s.contract.right is OptionRight.PUT}


def build_spreads(
    shorts: Sequence, snapshots: Sequence[ContractSnapshot], criteria: ScanCriteria, positions: list,
) -> list[SpreadResult]:
    """Spreads que cumplen `criteria` (anchos, crédito/ancho, pérdida máxima y retorno sobre riesgo)."""
    index = _long_leg_index(snapshots)
    out: list[SpreadResult] = []
    for r in shorts:
        sc = r.snapshot.contract
        sell = r.reference_price
        if sell is None:
            continue
        for width in criteria.spread_widths:
            long = index.get((sc.ticker, sc.expiry, round(sc.strike - width, 4)))
            if long is None or long.ask is None or long.ask <= 0 or mid_price(long.bid, long.ask) is None:
                continue
            credit = sell - long.ask
            max_loss_ps = width - credit
            if credit <= EPS or max_loss_ps <= EPS:
                continue
            credit_width = credit / width * 100
            ror = credit / max_loss_ps * 100
            max_loss = max_loss_ps * sc.multiplier
            if criteria.min_credit_width_pct is not None and credit_width < criteria.min_credit_width_pct - EPS:
                continue
            if criteria.max_spread_loss is not None and max_loss > criteria.max_spread_loss + EPS:
                continue
            if criteria.min_return_on_risk_pct is not None and ror < criteria.min_return_on_risk_pct - EPS:
                continue
            breakeven = sc.strike - credit
            price = r.info.underlying_price if r.info else None
            impact = candidate_impact(positions, r.info.sector if r.info else None, width, sc.multiplier, sc.expiry)
            out.append(SpreadResult(
                r, long, width, credit, max_loss, credit_width, ror, ror * 365 / r.dte if r.dte > 0 else 0.0,
                breakeven, (price - breakeven) / price * 100 if price else None, impact.assignment_pct_of_portfolio,
            ))
    out.sort(key=lambda s: s.return_on_risk_annualized_pct, reverse=True)
    return out
