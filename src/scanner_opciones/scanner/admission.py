"""Admisión a la Watchlist: qué tickers del Universo merecen entrar (y cuáles de la Watchlist ya no merecen quedarse) según dos estilos.

- **TPC** (The Premium Collector): empresas de calidad que se querrían tener, con descuento y tendencia positiva.
- **Tastytrade**: venta de volatilidad, con IV Rank o IV Percentile altos y opciones líquidas.
- **Fuente directa**: las fuentes que ya se consultan varias veces al día y se supone que cumplen (listas de tastytrade, radar de Tortuga) pasan sin más filtro.

Son funciones puras: devuelven el MOTIVO por el que un ticker no cumple (None = cumple). Un dato que falta cuenta como «no cumple»."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from scanner_opciones.config.settings import AdmissionTastytrade, AdmissionTpc
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.technical import long_term_uptrend
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.quality import DEFAULT_EXEMPT, ticker_quality_reject

SESSIONS_52W = 252


def pullback_pct(closes: Sequence[float], window: int = SESSIONS_52W) -> Optional[float]:
    """Cuánto está el último cierre por debajo de su máximo de las últimas `window` sesiones (%, ≥ 0); None sin cierres."""
    if not closes:
        return None
    high = max(closes[-window:])
    return max((high - closes[-1]) / high * 100, 0.0) if high > 0 else None


def tpc_quality_criteria(cfg: AdmissionTpc, manageable: tuple[float, float] = (0.5, 3.0)) -> ScanCriteria:
    """Los filtros de calidad de la empresa que exige el estilo TPC, sobre unos criterios sin ningún otro filtro. `manageable` = (deuda/patrimonio máximo,
    cobertura de intereses mínima) de «deuda baja o manejable»."""
    return ScanCriteria(
        strike_below_pct_min=0, strike_below_pct_max=100, min_yield_pct=0, dte_min=0, dte_max=9999,     # el escaneo no se usa: solo sus filtros de calidad
        require_profitable=cfg.require_profitable, min_positive_quarters=cfg.min_positive_quarters or None,
        require_positive_fcf=cfg.require_positive_fcf, require_manageable_debt=cfg.require_manageable_debt,
        manageable_max_de=manageable[0], manageable_min_cover=manageable[1], min_option_liquidity=cfg.min_option_liquidity)


def tpc_quality_reject(info: Optional[TickerInfo], cfg: AdmissionTpc, exempt: Sequence[str] = DEFAULT_EXEMPT,
                       manageable: tuple[float, float] = (0.5, 3.0)) -> Optional[str]:
    return ticker_quality_reject(info, tpc_quality_criteria(cfg, manageable), exempt)


def tpc_trend_reject(closes: Sequence[float], cfg: AdmissionTpc, ma: int = 200, slope_days: int = 20, fallback_ma: int = 100) -> Optional[str]:
    """Tendencia positiva y descuento (distancia al máximo de 52 semanas). Sin cierres no se puede medir y no cumple."""
    if not (cfg.require_uptrend or cfg.min_pullback_pct):
        return None
    if not closes:
        return "sin histórico de cierres para medir la tendencia y el descuento"
    if cfg.require_uptrend:
        ok, note = long_term_uptrend(closes, ma, slope_days, fallback_ma)
        if ok is None:
            return f"tendencia: {note}"
        if not ok:
            return f"sin tendencia alcista: {note}"
    if cfg.min_pullback_pct:
        pb = pullback_pct(closes)
        if pb is None or pb < cfg.min_pullback_pct:
            return f"sin descuento: a {0 if pb is None else pb:.1f} % de su máximo de 52 semanas (mínimo {cfg.min_pullback_pct:g} %)"
    return None


def tasty_reject(iv_rank: Optional[float], iv_percentile: Optional[float], liquidity: Optional[int], cfg: AdmissionTastytrade) -> Optional[str]:
    """IV Rank / IV Percentile y liquidez de opciones del estilo Tastytrade. Con `iv_rank_pct_any` basta con uno de los dos."""
    if liquidity is None or liquidity < cfg.min_option_liquidity:
        return f"liquidez de opciones inferior a {cfg.min_option_liquidity} (o sin dato)"
    rank_ok = iv_rank is not None and iv_rank >= cfg.min_iv_rank
    pct_ok = iv_percentile is not None and iv_percentile >= cfg.min_iv_percentile
    if cfg.iv_rank_pct_any:
        if not (rank_ok or pct_ok):
            return (f"IV Rank {_f(iv_rank)} (mín. {cfg.min_iv_rank:g}) e IV Percentile {_f(iv_percentile)} (mín. {cfg.min_iv_percentile:g}) por debajo")
    elif not (rank_ok and pct_ok):
        return f"IV Rank {_f(iv_rank)} / IV Percentile {_f(iv_percentile)} por debajo de {cfg.min_iv_rank:g} / {cfg.min_iv_percentile:g}"
    return None


def _f(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}"


@dataclass(frozen=True)
class Verdict:
    """Resultado de un ticker: `tpc` y `tasty` son el motivo por el que NO cumple ese estilo (None = cumple); `direct` = viene de una fuente directa."""
    ticker: str
    in_watchlist: bool
    sources: tuple[str, ...] = ()
    direct: bool = False
    tpc: Optional[str] = "sin evaluar"
    tasty: Optional[str] = "sin evaluar"
    protected: bool = False          # tiene posiciones: nunca se propone quitarla

    @property
    def styles(self) -> list[str]:
        out = []
        if self.direct:
            out.append("Directa")
        if self.tpc is None:
            out.append("TPC")
        if self.tasty is None:
            out.append("Tastytrade")
        return out

    @property
    def passes(self) -> bool:
        return bool(self.styles)

    @property
    def why_not(self) -> str:
        return f"TPC: {self.tpc} · Tastytrade: {self.tasty}"


@dataclass(frozen=True)
class AdmissionReport:
    generated_at: object                      # datetime
    verdicts: tuple[Verdict, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def to_add(self) -> list[Verdict]:
        """Del Universo y aún fuera de la Watchlist, y cumplen algún estilo."""
        return [v for v in self.verdicts if not v.in_watchlist and v.passes]

    @property
    def to_drop(self) -> list[Verdict]:
        """En la Watchlist y ya no cumplen ningún estilo (las protegidas por posiciones se muestran, pero no se pueden quitar)."""
        return [v for v in self.verdicts if v.in_watchlist and not v.passes]

    def rejected_counts(self) -> dict[str, int]:
        """Cuántos candidatos (fuera de la Watchlist) descarta cada motivo principal, para ver qué filtro pesa más."""
        counts: dict[str, int] = {}
        for v in self.verdicts:
            if not v.in_watchlist and not v.passes:
                key = (v.tpc or "").split(":")[0].split(" (")[0][:60]
                counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
