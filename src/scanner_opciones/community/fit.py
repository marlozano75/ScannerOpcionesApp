"""¿Encaja una idea de la comunidad en alguno de los perfiles del Scanner (TPC Mensual, TPC Táctico, Tastytrade)?

Se compara con los valores de HOY (DTE, distancia del strike, yield y delta de la cotización actual) y con los valores por defecto de
cada perfil (`scanner.presets` y `scanner.strategy`). Solo se comprueban los criterios que una idea permite medir: descuento del strike,
DTE, yield del periodo (solo en la put desnuda: en un spread el yield es sobre el ancho y no es comparable), delta, IV Rank e IV Percentile
(solo si el ticker tiene ficha). No se evalúan los técnicos (RSI, medias, ATR…), la calidad de la empresa ni la liquidez: «encaja» significa
«cumple lo medible», y lo que no se pudo medir se indica aparte.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.config.settings import ScannerPreset, StrategySettings

STRATEGY_NAME = "Tastytrade"


@dataclass(frozen=True)
class Fit:
    name: str
    fits: bool                   # cumple todo lo que se pudo medir (y al menos descuento y DTE)
    failed: tuple[str, ...]      # criterios medidos que no cumple
    unknown: tuple[str, ...]     # criterios sin dato o no comparables

    @property
    def detail(self) -> str:
        parts = []
        if self.failed:
            parts.append("No cumple: " + "; ".join(self.failed))
        if self.unknown:
            parts.append("Sin comprobar: " + "; ".join(self.unknown))
        return ". ".join(parts)


def _check(failed: list[str], unknown: list[str], label: str, value: Optional[float], ok, shown: str) -> None:
    if value is None:
        unknown.append(label)
    elif not ok(value):
        failed.append(f"{label} {shown}")


def _fit(name: str, view, *, discount_min: float, dte_min: int, dte_max: Optional[int], yield_min: float,
         delta_min: Optional[float] = None, delta_max: Optional[float] = None,
         iv_rank_min: Optional[float] = None, iv_pct_min: Optional[float] = None) -> Fit:
    failed: list[str] = []
    unknown: list[str] = []
    _check(failed, unknown, "descuento", view.otm_now_pct, lambda v: v >= discount_min, f"{view.otm_now_pct or 0:.1f} % (mín. {discount_min:g} %)")
    top = "" if dte_max is None else f"–{dte_max}"
    if not (view.dte >= dte_min and (dte_max is None or view.dte <= dte_max)):
        failed.append(f"DTE {view.dte} (rango {dte_min}{top})")
    if view.idea.long_strike is not None:
        unknown.append("yield (en un spread es sobre el ancho)")
    else:
        _check(failed, unknown, "yield", view.yield_now_pct, lambda v: v >= yield_min, f"{view.yield_now_pct or 0:.2f} % (mín. {yield_min:g} %)")
    if delta_max is not None:
        d = None if view.delta_now is None else abs(view.delta_now)
        _check(failed, unknown, "delta", d, lambda v: v <= delta_max and (delta_min is None or v >= delta_min), f"{d or 0:.2f} (máx. {delta_max:g})")
    if iv_rank_min is not None:
        _check(failed, unknown, "IV Rank", view.iv_rank, lambda v: v >= iv_rank_min, f"{view.iv_rank or 0:.0f} (mín. {iv_rank_min:g})")
    if iv_pct_min is not None:
        _check(failed, unknown, "IV Percentile", view.iv_percentile, lambda v: v >= iv_pct_min, f"{view.iv_percentile or 0:.0f} (mín. {iv_pct_min:g})")
    core_known = view.otm_now_pct is not None
    return Fit(name, not failed and core_known, tuple(failed), tuple(unknown))


def strategy_fits(view, presets: Sequence[ScannerPreset], strategy: StrategySettings) -> tuple[Fit, ...]:
    """Un `Fit` por perfil: los TPC (`presets`) y el de Tastytrade (`strategy`). Vacío si la idea ya venció."""
    if view.dte < 0:
        return ()
    out = [_fit(p.name, view, discount_min=p.strike_below_pct_min, dte_min=p.dte_min, dte_max=p.dte_max, yield_min=p.min_yield_pct)
           for p in presets]
    out.append(_fit(STRATEGY_NAME, view, discount_min=strategy.strike_below_pct_min, dte_min=strategy.dte_min, dte_max=strategy.dte_max,
                    yield_min=strategy.min_yield_pct, delta_min=strategy.min_abs_delta, delta_max=strategy.max_abs_delta,
                    iv_rank_min=strategy.min_iv_rank, iv_pct_min=strategy.min_iv_percentile))
    return tuple(out)
