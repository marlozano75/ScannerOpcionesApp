"""Panel «¿Cuánto descarta cada filtro?» del perfil Call Debit Spread (MTO CDS). Funciones puras.

Mismo informe que `scanner/impact.py` (`ImpactReport`), pero sobre spreads y no sobre contratos: la «ventana» son los CDS que existen dentro de
la ventana del catálogo (DTE, % OTM de la comprada, anchos elegidos, con delta/IV y mercado válido en las dos patas) sin ningún otro filtro. Se
construye UNA vez con todos los filtros opcionales relajados y cada filtro se evalúa como predicado sobre esa lista (en vez de repetir el
escaneo entero por filtro). Se cuentan TODOS los anchos (`best_width` apagado): el recuento de la tabla puede ser menor si «mejor ancho» está activo."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Optional, Sequence

from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.scanner.cds import EPS, CdsResult, _size_ok, _spread_pct, build_call_debit_spreads, earnings_in_window
from scanner_opciones.scanner.impact import FilterImpact, ImpactReport

HUGE = 1e12

Predicate = Callable[[CdsResult], bool]


@dataclass(frozen=True)
class TickerFilter:
    """Filtro del ticker (calidad de la empresa, liquidez de opciones, precio, fuentes): se aplica antes de buscar spreads, pero en el panel cuenta
    como un filtro más (sobre los spreads de los tickers que pasan)."""
    key: str
    label: str
    config: str
    passes: Callable[[str], bool]


def _g(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def relaxed(cds: CdsSettings) -> CdsSettings:
    """Los mismos CDS pero sin ningún filtro opcional (queda la ventana del catálogo y la validez de las patas)."""
    return cds.model_copy(update=dict(
        best_width=False, exclude_earnings=False, top_per_ticker=0, min_prob_pct=None, min_net_delta=None, min_exit_profit_usd=None, min_debit_usd=0.0, max_debit_usd=HUGE, min_long_oi=0, min_short_oi=0, max_long_spread_pct=HUGE, max_short_spread_pct=HUGE, min_bid_size=None, min_ask_size=None, min_long_volume=None, min_short_volume=None, max_required_move_mult=HUGE,
        min_max_return_pct=None, sma200_below_pct_min=None, width_pct_min=None, width_pct_max=None))


def predicates(cds: CdsSettings, infos: Optional[Mapping[str, TickerInfo]] = None, today: Optional[date] = None) -> list[tuple[str, str, str, Predicate]]:
    """(clave, etiqueta, configuración en palabras, predicado) de cada filtro ACTIVO."""
    mult = lambda r: r.long.contract.multiplier
    out: list[tuple[str, str, str, Predicate]] = [
        ("cds_debit", "Débito por contrato", f"{_g(cds.min_debit_usd)}–{_g(cds.max_debit_usd)} $",
         lambda r: cds.min_debit_usd - EPS <= r.debit * mult(r) <= cds.max_debit_usd + EPS),
        ("cds_move", "Movimiento necesario ≤ esperado", f"≤ {_g(cds.max_required_move_mult)}× (objetivo {_g(cds.target_gain_pct)} % en {cds.move_days} d)",
         lambda r: r.required_move_mult <= cds.max_required_move_mult + EPS),
        *([("cds_prob", "Probabilidad de alcanzar el objetivo", f"≥ {_g(cds.min_prob_pct)} %", lambda r: r.prob_pct >= cds.min_prob_pct - EPS)]
          if cds.min_prob_pct is not None else []),
        *([("cds_ndelta", "Delta neta", f"≥ {_g(cds.min_net_delta)}", lambda r: r.net_delta >= cds.min_net_delta - EPS)]
          if cds.min_net_delta is not None else []),
        *([("cds_xprofit", "Ganancia de salida", f"≥ {_g(cds.min_exit_profit_usd)} $", lambda r: r.exit_profit_usd >= cds.min_exit_profit_usd - EPS)]
          if cds.min_exit_profit_usd is not None else []),
        ("cds_spread", "Spread bid/ask de la comprada", f"≤ {_g(cds.max_long_spread_pct)} %",
         lambda r: (sp := _spread_pct(r.long)) is not None and sp <= cds.max_long_spread_pct + EPS),
        ("cds_spread_s", "Spread bid/ask de la vendida", f"≤ {_g(cds.max_short_spread_pct)} %",
         lambda r: (sp := _spread_pct(r.short)) is not None and sp <= cds.max_short_spread_pct + EPS),
    ]
    if cds.exclude_earnings and infos is not None and today is not None:
        out.append(("cds_earn", "Sin resultados en la ventana", f"ninguno en los próximos {cds.move_days} d",
                    lambda r: not earnings_in_window(infos.get(r.long.contract.ticker), cds.move_days, today)))
    if cds.min_long_oi > 0:
        out.append(("cds_oi", "OI de la comprada", f"≥ {cds.min_long_oi}", lambda r: (r.long.open_interest or 0) >= cds.min_long_oi))
    if cds.min_short_oi > 0:
        out.append(("cds_oi_s", "OI de la vendida", f"≥ {cds.min_short_oi}", lambda r: (r.short.open_interest or 0) >= cds.min_short_oi))
    if cds.min_long_volume is not None:
        out.append(("cds_lvol", "Volumen de la comprada", f"≥ {cds.min_long_volume}", lambda r: _size_ok(r.long.volume, cds.min_long_volume)))
    if cds.min_short_volume is not None:
        out.append(("cds_svol", "Volumen de la vendida", f"≥ {cds.min_short_volume}", lambda r: _size_ok(r.short.volume, cds.min_short_volume)))
    if cds.min_bid_size is not None:
        out.append(("cds_bsz", "Bid size de cada pata", f"≥ {cds.min_bid_size}", lambda r: all(_size_ok(s.bid_size, cds.min_bid_size) for s in (r.long, r.short))))
    if cds.min_ask_size is not None:
        out.append(("cds_asz", "Ask size de cada pata", f"≥ {cds.min_ask_size}", lambda r: all(_size_ok(s.ask_size, cds.min_ask_size) for s in (r.long, r.short))))
    if cds.width_pct_min is not None or cds.width_pct_max is not None:
        lo, hi = cds.width_pct_min, cds.width_pct_max

        def width_ok(r: CdsResult) -> bool:
            pct = r.width / r.long.contract.strike * 100
            return (lo is None or pct >= lo - EPS) and (hi is None or pct <= hi + EPS)
        out.append(("cds_wpct", "Ancho / strike", f"{_g(lo) if lo is not None else '0'}–{_g(hi) if hi is not None else '∞'} %", width_ok))
    if cds.min_max_return_pct is not None:
        out.append(("cds_return", "Retorno máximo", f"≥ {_g(cds.min_max_return_pct)} %", lambda r: r.max_return_pct >= cds.min_max_return_pct - EPS))
    if cds.sma200_below_pct_min is not None:
        out.append(("cds_sma", "Precio bajo la SMA200", f"≥ {_g(cds.sma200_below_pct_min)} % por debajo",
                    lambda r: r.sma200_distance_pct is not None and r.sma200_distance_pct <= -cds.sma200_below_pct_min + EPS))
    return out


def cds_impact_report(
    snapshots: Sequence[ContractSnapshot], infos: Mapping[str, TickerInfo], cds: CdsSettings, today: date,
    bars: Optional[Mapping[str, Sequence]] = None, price_reference: PriceReference = PriceReference.BID_PLUS_SPREAD,
    price_spread_pct: float = 25.0, cost_ps: Callable[[float], float] = lambda multiplier: 0.0,
    ticker_filters: Sequence[TickerFilter] = (),
) -> ImpactReport:
    window = build_call_debit_spreads(snapshots, infos, relaxed(cds), today, bars, price_reference, price_spread_pct, cost_ps)
    preds = predicates(cds, infos, today) + [(f.key, f.label, f.config, (lambda r, f=f: f.passes(r.long.contract.ticker))) for f in ticker_filters]
    final = [r for r in window if all(p(r) for _, _, _, p in preds)]
    rows = []
    for key, label, config, pred in preds:
        alone = sum(1 for r in window if pred(r))
        others = [p for k, _, _, p in preds if k != key]
        without = sum(1 for r in window if all(p(r) for p in others))
        rows.append(FilterImpact(key, label, alone, without - len(final), config))
    rows.sort(key=lambda r: (r.alone, -r.exclusive))
    conflicts: tuple[tuple[str, str], ...] = ()
    if not final and len(rows) > 1:
        sets = {key: {i for i, r in enumerate(window) if pred(r)} for key, _, _, pred in preds}
        labels = {f.key: f.label for f in rows}
        keys = [f.key for f in rows]
        conflicts = tuple((labels[a], labels[b]) for i, a in enumerate(keys) for b in keys[i + 1:]
                          if sets[a] and sets[b] and not (sets[a] & sets[b]))[:6]
    return ImpactReport(len(window), len(final), tuple(rows), conflicts)
