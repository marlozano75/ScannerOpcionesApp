"""Cuánto descarta cada filtro del scanner: sirve para ver lo restrictiva que es una selección antes de fiarse de ella.

Para cada filtro activo se calculan dos números sobre la «ventana» (los contratos con descuento y DTE dentro del rango
y una cotización válida, sin ningún otro filtro):
  · `alone`: cuántos contratos de la ventana pasan con SOLO ese filtro (lo restrictivo que es por sí mismo);
  · `exclusive`: cuántos contratos descarta únicamente ese filtro, es decir, cuántos recuperarías al quitarlo y dejar
    los demás (lo que añade a los otros; 0 = redundante, los demás ya lo cubren).
Funciones puras sobre los snapshots; no se guarda nada.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import TechnicalSettings
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, SOLVENCY_RULES, ScanCriteria
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.scanner.quality import DEFAULT_EXEMPT, quality_reject
from scanner_opciones.scanner.signals import SIGNAL_FIELDS, signals_reject
from scanner_opciones.scanner.technical import TechnicalFilter

# (clave, etiqueta, {campo del criterio: valor que lo desactiva})
_GROUPS: tuple[tuple[str, str, dict], ...] = (
    ("yield", "Yield mínimo del periodo", {"min_yield_pct": 0.0}),
    ("price", "Precio del subyacente", {"min_price": None, "max_price": None}),
    ("oi", "OI mínimo", {"min_oi": None}),
    ("bid_size", "Bid size mínimo", {"min_bid_size": None}),
    ("spread", "Spread máximo", {"max_spread_pct": None}),
    ("iv_rank", "IV Rank mínimo", {"min_iv_rank": None}),
    ("iv_pct", "IV Percentile mínimo", {"min_iv_percentile": None}),
    ("iv_hv", "IV / HV30 mínimo", {"min_iv_hv_ratio": None}),
    ("iv_change", "Cambio de IV a 5 días", {"max_iv_change_5d": None}),
    ("atr", "Strike a N ATR del precio", {"min_atr_multiple": None}),
    ("delta", "Delta del put", {"min_abs_delta": None, "max_abs_delta": None}),
    ("near_level", "Cerca de la media de 50 o de un soporte", {"near_level_pct": None}),
    ("rsi", "RSI rebotando desde el nivel de sobreventa", {"rsi_rebound_max": None}),
    ("bollinger", "Bollinger: cerca de la banda inferior", {"max_bollinger_pctb": None}),
    ("bollinger_width", "Bollinger: bandas no estrechas", {"min_bollinger_width_pct": None}),
    ("signals", "Señales de entrada (al menos N)", {"signals_required": 0, **{f: None for f in SIGNAL_FIELDS}}),
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
    config: str = ""    # su configuración actual en palabras («≥ 50», «entre 0,2 y 0,3»…)


@dataclass(frozen=True)
class ImpactReport:
    window: int                         # contratos dentro del rango de descuento y DTE con cotización válida
    final: int                          # los que pasan TODOS los filtros activos
    filters: tuple[FilterImpact, ...]   # solo los filtros activos, del más restrictivo al menos
    conflicts: tuple[tuple[str, str], ...] = ()   # con 0 resultados: pares de filtros que, aunque cada uno deja contratos, no comparten ninguno


_SIGNAL_GROUPS = {"rsi", "bollinger", "near_level"}   # con «al menos N señales» cuentan como un único grupo


def _g(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _range(lo, hi, unit: str = "") -> str:
    if lo is not None and hi is not None:
        return f"entre {_g(lo)}{unit} y {_g(hi)}{unit}"
    return f"≥ {_g(lo)}{unit}" if lo is not None else f"≤ {_g(hi)}{unit}"


_SOLVENCY = {field: (op, label) for field, _, op, label in SOLVENCY_RULES}
_MA_SIDE = {"above": "precio sobre", "below": "precio bajo", "gte": "≥", "lte": "≤", "up": "sube", "down": "baja"}
_TREND = {"up": "alcista", "down": "bajista"}


def describe_group(key: str, c: ScanCriteria) -> str:
    """La configuración actual de un grupo de filtros, para mostrarla junto a su nombre."""
    if key in _SOLVENCY:
        op, label = _SOLVENCY[key]
        return f"{label} {'≤' if op == 'max' else '≥'} {_g(getattr(c, key))}"
    text = {
        "yield": lambda: f"≥ {_g(c.min_yield_pct)} %",
        "price": lambda: _range(c.min_price, c.max_price, " $"),
        "oi": lambda: f"≥ {c.min_oi}",
        "bid_size": lambda: f"≥ {c.min_bid_size}",
        "spread": lambda: f"≤ {_g(c.max_spread_pct)} %",
        "iv_rank": lambda: f"≥ {_g(c.min_iv_rank)}",
        "iv_pct": lambda: f"≥ {_g(c.min_iv_percentile)}",
        "iv_hv": lambda: f"≥ {_g(c.min_iv_hv_ratio)}",
        "iv_change": lambda: f"≤ {_g(c.max_iv_change_5d)} puntos",
        "atr": lambda: f"strike a ≥ {_g(c.min_atr_multiple)} ATR",
        "delta": lambda: "|Δ| " + _range(c.min_abs_delta, c.max_abs_delta),
        "near_level": lambda: f"a ≤ {_g(c.near_level_pct)} % de la MA50 o del soporte",
        "rsi": lambda: f"tocó ≤ {_g(c.rsi_rebound_max)} y sube",
        "bollinger": lambda: f"%B ≤ {_g(c.max_bollinger_pctb)}",
        "bollinger_width": lambda: f"ancho ≥ {_g(c.min_bollinger_width_pct)} %",
        "profit": lambda: "EPS 12 meses > 0",
        "quarters": lambda: f"≥ {c.min_positive_quarters} de 4",
        "liquidity": lambda: f"≥ {c.min_option_liquidity}",
        "earnings": lambda: "sin resultados entre hoy y el vencimiento",
        "leverage": lambda: f"≤ {_g(c.max_liabilities_to_equity)}",
        "fcf": lambda: "FCF 12 meses > 0",
        "manageable": lambda: f"deuda/patrimonio ≤ {_g(c.manageable_max_de)} o cobertura ≥ {_g(c.manageable_min_cover)}×",
        "trend": lambda: f"{_TREND.get(c.trend_direction, c.trend_direction)}, últimos {c.trend_window_months} meses",
        "support": lambda: "con zona de soporte",
        "touch": lambda: f"≥ {c.min_days_since_touch} días",
    }.get(key)
    if text is not None:
        return text()
    if key == "signals":
        parts = [f"{label} {describe_group(k, c)}" for k, label, fields in _GROUPS if k in _SIGNAL_GROUPS
                 and any(getattr(c, f) is not None for f in fields)]
        return f"al menos {c.signals_required} de: " + "; ".join(parts)
    if key == "averages":
        names = {**{f: f.upper() for f in MA_LINES}, **{f: f"{a.upper()}/{b.upper()}" for f, (a, b) in MA_CROSSES.items()},
                 **{f: f"pendiente {k.upper()}" for f, k in MA_SLOPES.items()}}
        parts = [f"{names[f]} {_MA_SIDE.get(getattr(c, f), getattr(c, f))}" for f in names if getattr(c, f) != "any"]
        return "; ".join(parts) + f" (velas {c.ma_frame})"
    return ""


def active_groups(criteria: ScanCriteria) -> list[tuple[str, str, dict]]:
    """Los grupos de filtros que `criteria` tiene activos (algún campo distinto del valor neutro)."""
    mode = criteria.signals_required > 0
    out = []
    for g in _GROUPS:
        if g[0] == "signals":
            if mode and any(getattr(criteria, f) is not None for f in SIGNAL_FIELDS):
                out.append(g)
        elif not (mode and g[0] in _SIGNAL_GROUPS) and any(getattr(criteria, f) != neutral for f, neutral in g[2].items()):
            out.append(g)
    return out


def _neutral(criteria: ScanCriteria) -> ScanCriteria:
    return criteria.with_filters(**{f: n for _, _, fields in _GROUPS for f, n in fields.items()})


def _passes(
    snapshots: Sequence[ContractSnapshot], infos: dict[str, TickerInfo], criteria: ScanCriteria, today: date,
    bars: Optional[dict], technical: Optional[TechnicalSettings], exempt: Optional[Sequence[str]],
    atr: Optional[dict] = None,
) -> list[ContractSnapshot]:
    tech = TechnicalFilter(criteria, technical or TechnicalSettings(), bars or {}, today) if criteria.technical_active else None
    keep = exempt if exempt is not None else DEFAULT_EXEMPT
    out = []
    for snap in snapshots:
        c = snap.contract
        info = infos.get(c.ticker)
        price = info.underlying_price if info else None
        if reject_reason(snap, price, today, criteria, snap.iv_rank, snap.iv_percentile, info,
                         (atr or {}).get(c.ticker)) is not None:
            continue
        if criteria.quality_active and quality_reject(info, c.expiry, criteria, today, keep) is not None:
            continue
        if tech is not None and tech.reject(c.ticker, price, c.strike) is not None:
            continue
        if signals_reject(criteria, info, tech, c.ticker, price) is not None:
            continue
        out.append(snap)
    return out


def impact_report(
    snapshots: Sequence[ContractSnapshot], infos: dict[str, TickerInfo], criteria: ScanCriteria, today: date,
    bars: Optional[dict] = None, technical: Optional[TechnicalSettings] = None,
    exempt_sectors: Optional[Sequence[str]] = None, atr: Optional[dict] = None,
) -> ImpactReport:
    groups = active_groups(criteria)
    neutral = _neutral(criteria)
    window = _passes(snapshots, infos, neutral, today, bars, technical, exempt_sectors, atr)   # el resto solo mira estos
    final = _passes(window, infos, criteria, today, bars, technical, exempt_sectors, atr)
    rows = []
    alone_sets: dict[str, set] = {}
    for key, label, fields in groups:
        alone = _passes(window, infos, neutral.with_filters(**{f: getattr(criteria, f) for f in fields}),
                        today, bars, technical, exempt_sectors, atr)
        without = _passes(window, infos, criteria.with_filters(**fields), today, bars, technical, exempt_sectors, atr)
        rows.append(FilterImpact(key, label, len(alone), len(without) - len(final), describe_group(key, criteria)))
        alone_sets[key] = {(s.contract.ticker, s.contract.expiry, s.contract.strike) for s in alone}
    rows.sort(key=lambda r: (r.alone, -r.exclusive))
    conflicts: tuple[tuple[str, str], ...] = ()
    if not final and len(rows) > 1:
        conflicts = tuple((a.label, b.label) for a, b in itertools.combinations(rows, 2)
                          if alone_sets[a.key] and alone_sets[b.key] and not (alone_sets[a.key] & alone_sets[b.key]))[:6]
    return ImpactReport(len(window), len(final), tuple(rows), conflicts)


def explain_empty(report: ImpactReport) -> Optional[str]:
    """Frase que explica por qué no sale ningún contrato (o None si sale alguno o no hay filtros): qué filtro, quitado solo, devolvería contratos, qué
    filtros se estorban entre sí o, si no, cuáles son los más restrictivos."""
    if report.final > 0 or not report.filters:
        return None
    head = f"Ningún contrato pasa todos los filtros (hay {report.window} dentro del rango de strike y DTE)."
    recover = sorted((f for f in report.filters if f.exclusive > 0), key=lambda f: -f.exclusive)
    if recover:
        return head + " Quitando solo " + "; o solo ".join(f"«{f.label}» recuperarías {f.exclusive}" for f in recover[:3]) + "."
    parts = []
    if report.conflicts:
        parts.append("Se estorban entre sí: " + "; ".join(f"«{a}» × «{b}»" for a, b in report.conflicts[:3]) + ".")
    tight = [f for f in report.filters if report.window and f.alone * 4 < report.window][:3]
    if tight:
        parts.append("Los más restrictivos: " + ", ".join(f"«{f.label}» (por sí solo deja {f.alone})" for f in tight) + ".")
    return " ".join([head, *parts, "Quitar un solo filtro no basta: relaja varios a la vez."])
