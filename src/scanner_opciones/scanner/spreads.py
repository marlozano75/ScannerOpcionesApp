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
from scanner_opciones.portfolio.diversification import candidate_impact, nominal
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


def combo_spread_pct(short: ContractSnapshot, long: ContractSnapshot, credit: float) -> float:
    """Spread bid/ask del combo en % del crédito: combo_bid = bid_v − ask_c y combo_ask = ask_v − bid_c, así que mide spread_v + spread_c.
    Un combo ancho frente a su crédito es un crédito que no se consigue (el precio de referencia depende de cada pata)."""
    if short.bid is None or short.ask is None or long.ask is None or credit <= 0:
        return float("inf")
    return ((short.ask - (long.bid or 0.0)) - (short.bid - long.ask)) / credit * 100


def _long_leg_index(snapshots: Sequence[ContractSnapshot]) -> dict[tuple, ContractSnapshot]:
    return {(s.contract.ticker, s.contract.expiry, round(s.contract.strike, 4)): s
            for s in snapshots if s.contract.right is OptionRight.PUT}


@dataclass(frozen=True)
class MixedRow:
    """Fila del modo «Ambos»: una put desnuda (`spread is None`) o un spread, con el capital que inmoviliza."""
    kind: str                        # put | spread
    short: "object"                  # ScanResult de la pata vendida
    spread: Optional[SpreadResult]
    credit: float                    # por acción
    capital: Optional[float]         # margen what-if (put) o pérdida máxima (spread), en $
    return_on_capital_annualized_pct: Optional[float]
    weight_pct: Optional[float]      # peso de la acción sobre el NLV si te asignan, con esta posición (spread: su ancho)
    why_spread: str = ""             # por qué la put desnuda no cabe (solo en spreads)
    profiles: tuple = ()             # escaneo de todos los perfiles: nombres de los perfiles cuyos filtros cumple la fila


def ticker_exposure(positions: Sequence) -> dict[str, float]:
    """USD por ticker que ya tienes: acciones + nominal de las puts vendidas (en un spread, su ancho)."""
    out: dict[str, float] = {}
    for p in positions:
        if p.option is None:
            if p.quantity > 0:
                out[p.ticker] = out.get(p.ticker, 0.0) + p.market_value
        else:
            out[p.ticker] = out.get(p.ticker, 0.0) + nominal(p)
    return out


def build_mixed(
    shorts: Sequence, spreads: Sequence[SpreadResult], criteria: ScanCriteria, positions: Sequence, nlv_usd: Optional[float],
    naked_min_yield_pct: float = 0.0, spread_only: frozenset = frozenset(),
) -> list[MixedRow]:
    """Put desnuda si cumple los límites (peso acumulado sobre el NLV y strike máximo); si no, sus spreads. Orden: retorno anualizado sobre capital."""
    return build_mixed_sets(shorts, shorts, spreads, criteria, positions, nlv_usd, naked_min_yield_pct, spread_only)


def build_mixed_sets(
    naked_shorts: Sequence, spread_shorts: Sequence, spreads: Sequence[SpreadResult], criteria: ScanCriteria, positions: Sequence,
    nlv_usd: Optional[float], naked_min_yield_pct: float = 0.0, spread_only: frozenset = frozenset(),
) -> list[MixedRow]:
    """Modo «Ambos» con un juego de filtros para cada instrumento: sale como put desnuda la que pasa los filtros de put (`naked_shorts`) y su
    strike cabe en `max_naked_strike`; si no cabe, sale su spread, siempre que la vendida pase los filtros de spread (`spread_shorts`).
    Los tickers de `spread_only` (solo están en fuentes aptas para spreads) nunca salen como put desnuda: solo su spread, con cualquier strike."""
    held = ticker_exposure(positions)
    by_short: dict[int, list[SpreadResult]] = {}
    for sp in spreads:
        by_short.setdefault(id(sp.short), []).append(sp)
    limit = criteria.max_naked_strike
    rows: list[MixedRow] = []
    for r in naked_shorts:
        c = r.snapshot.contract
        if c.ticker in spread_only or (limit is not None and c.strike > limit):
            continue
        if (r.yield_ref_pct or 0.0) < naked_min_yield_pct:
            continue
        weight = (held.get(c.ticker, 0.0) + c.strike * c.multiplier) / nlv_usd * 100 if nlv_usd else None
        margin = r.snapshot.initial_margin
        ror = (r.reference_price * c.multiplier / margin * 365 / r.dte
               if margin and margin > 0 and r.reference_price and r.dte > 0 else None)
        rows.append(MixedRow("put", r, None, r.reference_price or 0.0, margin, ror * 100 if ror is not None else None, weight))
    for r in spread_shorts:
        c = r.snapshot.contract
        only = c.ticker in spread_only
        if not only and (limit is None or c.strike <= limit):
            continue
        reason = "solo spread (fuente apta para spreads)" if only else f"strike {c.strike:g} > {limit:g}"
        for sp in by_short.get(id(r), []):
            w = (held.get(c.ticker, 0.0) + sp.width * c.multiplier) / nlv_usd * 100 if nlv_usd else None
            rows.append(MixedRow("spread", r, sp, sp.credit, sp.max_loss, sp.return_on_risk_annualized_pct, w, reason))
    rows.sort(key=lambda x: (x.return_on_capital_annualized_pct is not None, x.return_on_capital_annualized_pct or 0.0), reverse=True)
    return rows


def build_profile_rows(
    put_hits: dict, spread_hits: dict, positions: Sequence, nlv_usd: Optional[float],
) -> list[MixedRow]:
    """Filas del escaneo de TODOS los perfiles: una por put desnuda (`put_hits`: clave -> (ScanResult, nombres de perfil)) y una por spread
    (`spread_hits`: clave -> (SpreadResult, nombres)), con los perfiles cuyos filtros cumple cada una. Mismo capital y retorno que el modo «Ambos»."""
    held = ticker_exposure(positions)
    rows: list[MixedRow] = []
    for r, names in put_hits.values():
        c = r.snapshot.contract
        weight = (held.get(c.ticker, 0.0) + c.strike * c.multiplier) / nlv_usd * 100 if nlv_usd else None
        margin = r.snapshot.initial_margin
        ror = (r.reference_price * c.multiplier / margin * 365 / r.dte
               if margin and margin > 0 and r.reference_price and r.dte > 0 else None)
        rows.append(MixedRow("put", r, None, r.reference_price or 0.0, margin, ror * 100 if ror is not None else None, weight, "", tuple(names)))
    for sp, names in spread_hits.values():
        c = sp.short.snapshot.contract
        w = (held.get(c.ticker, 0.0) + sp.width * c.multiplier) / nlv_usd * 100 if nlv_usd else None
        rows.append(MixedRow("spread", sp.short, sp, sp.credit, sp.max_loss, sp.return_on_risk_annualized_pct, w, "", tuple(names)))
    rows.sort(key=lambda x: (x.return_on_capital_annualized_pct is not None, x.return_on_capital_annualized_pct or 0.0), reverse=True)
    return rows


def build_spreads(
    shorts: Sequence, snapshots: Sequence[ContractSnapshot], criteria: ScanCriteria, positions: list,
    nlv_usd: Optional[float] = None,
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
            if criteria.min_long_oi is not None and (long.open_interest or 0) < criteria.min_long_oi:
                continue
            if criteria.max_long_spread_pct is not None:   # un ask sin bid no es un mercado: spread de 200 % o más
                if long.bid is None or long.bid <= 0 or (long.ask - long.bid) / mid_price(long.bid, long.ask) * 100 > criteria.max_long_spread_pct + EPS:
                    continue
            credit = sell - long.ask
            max_loss_ps = width - credit
            if credit <= EPS or max_loss_ps <= EPS:
                continue
            if criteria.max_combo_spread_pct is not None and combo_spread_pct(r.snapshot, long, credit) > criteria.max_combo_spread_pct + EPS:
                continue
            credit_width = credit / width * 100
            ror = credit / max_loss_ps * 100
            max_loss = max_loss_ps * sc.multiplier
            if criteria.min_credit_width_pct is not None and credit_width < criteria.min_credit_width_pct - EPS:
                continue
            if criteria.min_spread_credit is not None and credit * sc.multiplier < criteria.min_spread_credit - EPS:
                continue
            if criteria.max_spread_loss is not None and max_loss > criteria.max_spread_loss + EPS:
                continue
            if criteria.max_loss_nlv_pct is not None and nlv_usd and max_loss / nlv_usd * 100 > criteria.max_loss_nlv_pct + EPS:
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
    if criteria.best_width:   # ya ordenado: el primero de cada put vendida es el de mayor retorno
        seen: set[int] = set()
        out = [s for s in out if not (id(s.short) in seen or seen.add(id(s.short)))]
    return out
