"""Cuánto descarta cada filtro del scanner: sirve para ver lo restrictiva que es una selección antes de fiarse de ella.

Para cada filtro activo se calculan dos números sobre la «ventana» (los contratos con descuento y DTE dentro del rango
y una cotización válida, sin ningún otro filtro):
  · `alone`: cuántos contratos de la ventana pasan con SOLO ese filtro (lo restrictivo que es por sí mismo);
  · `exclusive`: cuántos contratos descarta únicamente ese filtro, es decir, cuántos recuperarías al quitarlo y dejar
    los demás (lo que añade a los otros; 0 = redundante, los demás ya lo cubren).
Funciones puras sobre los snapshots; no se guarda nada.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import TechnicalSettings
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, SOLVENCY_RULES, ScanCriteria
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.scanner.quality import DEFAULT_EXEMPT, quality_reject
from scanner_opciones.scanner.technical import TechnicalFilter

# (clave, etiqueta, {campo del criterio: valor que lo desactiva})
_GROUPS: tuple[tuple[str, str, dict], ...] = (
    ("yield", "Yield anual mínimo", {"min_annual_yield_pct": 0.0}),
    ("price", "Precio del subyacente", {"min_price": None, "max_price": None}),
    ("bid", "Bid mínimo", {"min_bid": None}),
    ("oi", "OI mínimo", {"min_oi": None}),
    ("bid_size", "Bid size mínimo", {"min_bid_size": None}),
    ("spread", "Spread máximo", {"max_spread_pct": None}),
    ("iv_rank", "IV Rank mínimo", {"min_iv_rank": None}),
    ("iv_pct", "IV Percentile mínimo", {"min_iv_percentile": None}),
    ("profit", "Beneficios (12 meses)", {"require_profitable": False}),
    ("quarters", "Trimestres con beneficios", {"min_positive_quarters": None}),
    ("liquidity", "Liquidez de las opciones", {"min_option_liquidity": None}),
    ("earnings", "Evitar resultados antes del vencimiento", {"avoid_earnings": False}),
    ("leverage", "Pasivo / patrimonio", {"max_liabilities_to_equity": None}),
    ("fcf", "Flujo de caja libre > 0", {"require_positive_fcf": False}),
    ("manageable", "Deuda baja o manejable", {"require_manageable_debt": False}),
    *((field, label[0].upper() + label[1:], {field: None}) for field, _, _, label in SOLVENCY_RULES),
    ("trend", "Tendencia", {"trend_direction": "off"}),
    ("support", "Zona de soporte", {"require_support": False}),
    ("touch", "Días desde el último toque", {"min_days_since_touch": None}),
    ("averages", "Medias móviles", {k: "any" for k in (*MA_LINES, *MA_CROSSES, *MA_SLOPES)}),
)


@dataclass(frozen=True)
class FilterImpact:
    key: str
    label: str
    alone: int          # contratos de la ventana que pasan con solo este filtro
    exclusive: int      # contratos que descarta únicamente este filtro


@dataclass(frozen=True)
class ImpactReport:
    window: int                         # contratos dentro del rango de descuento y DTE con cotización válida
    final: int                          # los que pasan TODOS los filtros activos
    filters: tuple[FilterImpact, ...]   # solo los filtros activos, del más restrictivo al menos


def active_groups(criteria: ScanCriteria) -> list[tuple[str, str, dict]]:
    """Los grupos de filtros que `criteria` tiene activos (algún campo distinto del valor neutro)."""
    return [g for g in _GROUPS if any(getattr(criteria, f) != neutral for f, neutral in g[2].items())]


def _neutral(criteria: ScanCriteria) -> ScanCriteria:
    return criteria.with_filters(**{f: n for _, _, fields in _GROUPS for f, n in fields.items()})


def _passes(
    snapshots: Sequence[ContractSnapshot], infos: dict[str, TickerInfo], criteria: ScanCriteria, today: date,
    bars: Optional[dict], technical: Optional[TechnicalSettings], exempt: Optional[Sequence[str]],
) -> list[ContractSnapshot]:
    tech = TechnicalFilter(criteria, technical or TechnicalSettings(), bars or {}, today) if criteria.technical_active else None
    keep = exempt if exempt is not None else DEFAULT_EXEMPT
    out = []
    for snap in snapshots:
        c = snap.contract
        info = infos.get(c.ticker)
        price = info.underlying_price if info else None
        if reject_reason(snap, price, today, criteria, snap.iv_rank, snap.iv_percentile) is not None:
            continue
        if criteria.quality_active and quality_reject(info, c.expiry, criteria, today, keep) is not None:
            continue
        if tech is not None and tech.reject(c.ticker, price, c.strike) is not None:
            continue
        out.append(snap)
    return out


def impact_report(
    snapshots: Sequence[ContractSnapshot], infos: dict[str, TickerInfo], criteria: ScanCriteria, today: date,
    bars: Optional[dict] = None, technical: Optional[TechnicalSettings] = None,
    exempt_sectors: Optional[Sequence[str]] = None,
) -> ImpactReport:
    groups = active_groups(criteria)
    neutral = _neutral(criteria)
    window = _passes(snapshots, infos, neutral, today, bars, technical, exempt_sectors)   # el resto solo mira estos
    final = _passes(window, infos, criteria, today, bars, technical, exempt_sectors)
    rows = []
    for key, label, fields in groups:
        alone = _passes(window, infos, neutral.with_filters(**{f: getattr(criteria, f) for f in fields}),
                        today, bars, technical, exempt_sectors)
        without = _passes(window, infos, criteria.with_filters(**fields), today, bars, technical, exempt_sectors)
        rows.append(FilterImpact(key, label, len(alone), len(without) - len(final)))
    rows.sort(key=lambda r: (r.alone, -r.exclusive))
    return ImpactReport(len(window), len(final), tuple(rows))
