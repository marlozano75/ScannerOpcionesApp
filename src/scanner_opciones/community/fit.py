"""¿Encaja una idea de la comunidad en alguno de los perfiles del Scanner (TPC Mensual, TPC Táctico, Tastytrade)?

Se compara con los valores de HOY (DTE, distancia del strike, yield y delta de la cotización actual) y con los filtros GRABADOS en cada
perfil del Scanner (`profile_fits`: el juego de put para una put y el de spread para un spread; `strategy_fits` queda con los valores por
defecto de `scanner.presets` y `scanner.strategy`). Solo se comprueban los criterios que una idea permite medir: descuento del strike,
DTE, yield del periodo (en un spread, en su lugar, el retorno sobre riesgo del periodo: crédito / (ancho − crédito), contra el mínimo de
`scanner.spreads`), delta, IV Rank e IV Percentile
(solo si el ticker tiene ficha) y el precio del subyacente (solo si el perfil del mismo nombre lo limita). No se evalúan los técnicos (RSI, medias, ATR…), la calidad de la empresa ni la liquidez: «encaja» significa
«cumple lo medible», y lo que no se pudo medir se indica aparte.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional, Sequence

from scanner_opciones.config.settings import ScannerPreset, StrategySettings

STRATEGY_NAME = "Tastytrade"


SHORT = {"Precio": "PRE", "Descuento": "DES", "DTE": "DTE", "Yield": "YLD", "Retorno/riesgo": "R/R", "Delta": "DEL", "IV Rank": "IVR", "IV Percentile": "IVP"}


@dataclass(frozen=True)
class Criterion:
    label: str                   # «Descuento», «DTE», «Yield»…
    ok: Optional[bool]           # True cumple, False no cumple, None sin dato o no comparable
    text: str                    # valor frente al límite, p. ej. «16,0 % (mín. 20 %)»

    @property
    def short(self) -> str:
        return SHORT.get(self.label, self.label[:3].upper())


@dataclass(frozen=True)
class Fit:
    name: str
    criteria: tuple[Criterion, ...]
    core_known: bool = True      # se pudo medir al menos la distancia del strike

    @property
    def passed(self) -> int:
        return sum(1 for c in self.criteria if c.ok)

    @property
    def total(self) -> int:
        return len(self.criteria)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(f"{c.label} {c.text}" for c in self.criteria if c.ok is False)

    @property
    def unknown(self) -> tuple[str, ...]:
        return tuple(c.label for c in self.criteria if c.ok is None)

    @property
    def fits(self) -> bool:
        """Cumple todo lo que se pudo medir (y al menos el descuento)."""
        return self.core_known and not self.failed

    @property
    def detail(self) -> str:
        parts = []
        if self.failed:
            parts.append("No cumple: " + "; ".join(self.failed))
        if self.unknown:
            parts.append("Sin comprobar: " + "; ".join(self.unknown))
        return ". ".join(parts)


def _crit(label: str, value: Optional[float], ok, shown: str) -> Criterion:
    return Criterion(label, None, "sin dato") if value is None else Criterion(label, bool(ok(value)), shown)


def _spread_return(view, minimum: Optional[float]) -> Criterion:
    """Retorno sobre riesgo del periodo de un spread (el equivalente a su yield): crédito / (ancho − crédito). `yield_now_pct` de un spread
    es crédito / ancho."""
    cw = view.yield_now_pct
    if minimum is None:
        return Criterion("Yield", None, "en un spread es sobre el ancho: no comparable")
    if cw is None or cw >= 100:
        return Criterion("Retorno/riesgo", None, "sin dato")
    ror = cw / (100 - cw) * 100
    return Criterion("Retorno/riesgo", ror >= minimum, f"{ror:.0f} % (mín. {minimum:g} %, crédito/ancho {cw:.0f} %)")


def _fit(name: str, view, *, discount_min: float, dte_min: int, dte_max: Optional[int], yield_min: float,
         delta_min: Optional[float] = None, delta_max: Optional[float] = None,
         iv_rank_min: Optional[float] = None, iv_pct_min: Optional[float] = None,
         price: tuple[Optional[float], Optional[float]] = (None, None), spread_min_ror: Optional[float] = None) -> Fit:
    out: list[Criterion] = []
    lo, hi = price
    if lo is not None or hi is not None:      # el filtro de precio del subyacente del perfil (en $); sin límites no se comprueba
        px = view.price_now
        limits = f"{'0' if lo is None else f'{lo:g}'}–{'∞' if hi is None else f'{hi:g}'} $"
        out.append(_crit("Precio", px, lambda v: (lo is None or v >= lo) and (hi is None or v <= hi), f"{px or 0:.2f} $ (rango {limits})"))
    d = view.otm_now_pct
    out.append(_crit("Descuento", d, lambda v: v >= discount_min, f"{d or 0:.1f} % (mín. {discount_min:g} %)"))
    top = "" if dte_max is None else f"–{dte_max}"
    out.append(Criterion("DTE", dte_min <= view.dte and (dte_max is None or view.dte <= dte_max), f"{view.dte} (rango {dte_min}{top})"))
    if view.idea.long_strike is not None:
        out.append(_spread_return(view, spread_min_ror))
    else:
        y = view.yield_now_pct
        out.append(_crit("Yield", y, lambda v: v >= yield_min, f"{y or 0:.2f} % (mín. {yield_min:g} %)"))
    if delta_max is not None:
        dl = None if view.delta_now is None else abs(view.delta_now)
        rng = f"máx. {delta_max:g}" if delta_min is None else f"{delta_min:g}–{delta_max:g}"
        out.append(_crit("Delta", dl, lambda v: v <= delta_max and (delta_min is None or v >= delta_min), f"{dl or 0:.2f} ({rng})"))
    if iv_rank_min is not None:
        out.append(_crit("IV Rank", view.iv_rank, lambda v: v >= iv_rank_min, f"{view.iv_rank or 0:.0f} (mín. {iv_rank_min:g})"))
    if iv_pct_min is not None:
        out.append(_crit("IV Percentile", view.iv_percentile, lambda v: v >= iv_pct_min, f"{view.iv_percentile or 0:.0f} (mín. {iv_pct_min:g})"))
    return Fit(name, tuple(out), d is not None)


def profile_fits(view, specs: Sequence[tuple], default_ror: Optional[float] = None) -> tuple[Optional[Fit], ...]:
    """Un `Fit` por perfil guardado, con los filtros ACTUALES de su juego de put (o de spread, si la idea es un spread). `specs` =
    (nombre, criterios de put | None, criterios de spread | None), en el orden de las columnas; un perfil sin ese instrumento da None.
    Vacío si la idea ya venció."""
    if view.dte < 0:
        return ()
    spread = view.idea.long_strike is not None
    out: list[Optional[Fit]] = []
    for name, put, spr in specs:
        c = spr if spread else put
        other = c is None                # el perfil no tiene ese instrumento: se compara con el juego del otro (descuento, DTE, delta, IV...)
        if other:
            c = put if spread else spr
        if c is None:
            out.append(None)
            continue
        fit = _fit(name, view, discount_min=c.strike_below_pct_min, dte_min=c.dte_min, dte_max=c.dte_max, yield_min=c.min_yield_pct,
                   delta_min=c.min_abs_delta, delta_max=c.max_abs_delta, iv_rank_min=c.min_iv_rank, iv_pct_min=c.min_iv_percentile,
                   price=(c.min_price, c.max_price), spread_min_ror=c.min_return_on_risk_pct if spread and not other else default_ror if spread else None)
        if other:                        # el rendimiento no es comparable entre una put (sobre el strike) y un spread (sobre el riesgo)
            fit = replace(fit, criteria=tuple(Criterion(k.label, None, "perfil de " + ("put" if spread else "spread") + ": no comparable")
                                              if k.label in ("Yield", "Retorno/riesgo") else k for k in fit.criteria))
        out.append(fit)
    return tuple(out)


def strategy_fits(view, presets: Sequence[ScannerPreset], strategy: StrategySettings,
                  prices: Optional[Mapping[str, tuple[Optional[float], Optional[float]]]] = None,
                  spread_min_ror: Optional[float] = None) -> tuple[Fit, ...]:
    """Un `Fit` por perfil: los TPC (`presets`) y el de Tastytrade (`strategy`). `prices` da el rango de precio del subyacente del perfil de
    cada nombre; `spread_min_ror` es el retorno mínimo sobre riesgo de los spreads (sin él, el criterio queda «no comparable»). Vacío si la idea ya venció."""
    prices = prices or {}
    if view.dte < 0:
        return ()
    out = [_fit(p.name, view, discount_min=p.strike_below_pct_min, dte_min=p.dte_min, dte_max=p.dte_max, yield_min=p.min_yield_pct, price=prices.get(p.name, (None, None)), spread_min_ror=spread_min_ror)
           for p in presets]
    out.append(_fit(STRATEGY_NAME, view, discount_min=strategy.strike_below_pct_min, dte_min=strategy.dte_min, dte_max=strategy.dte_max,
                    yield_min=strategy.min_yield_pct, delta_min=strategy.min_abs_delta, delta_max=strategy.max_abs_delta,
                    iv_rank_min=strategy.min_iv_rank, iv_pct_min=strategy.min_iv_percentile,
                    price=prices.get(STRATEGY_NAME, (None, None)), spread_min_ror=spread_min_ror))
    return tuple(out)
