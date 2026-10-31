"""¿Encaja un contrato de una fuente externa (ideas de la comunidad, radar de Tortuga) en alguno de los perfiles del Scanner?

Se compara con los valores de HOY (DTE, distancia del strike, yield y delta de la cotización actual) y con los filtros GRABADOS en cada
perfil del Scanner (`profile_fits`: el juego de put para una put y el de spread para un spread). No hay lista propia de perfiles: los que
haya guardados son las columnas, así que un perfil nuevo se compara sin tocar código. Solo se comprueban los criterios que una idea permite medir: descuento del strike,
DTE, yield del periodo (en un spread, en su lugar, el crédito/ancho que muestra la tabla, contra el mínimo y máximo de crédito/ancho del
perfil o, sin mínimo, el retorno sobre riesgo mínimo de `scanner.spreads` convertido), delta, IV Rank e IV Percentile
(solo si el ticker tiene ficha) y el precio del subyacente (solo si el perfil lo limita). No se evalúan los técnicos (RSI, medias, ATR…), la calidad de la empresa ni la liquidez: «encaja» significa
«cumple lo medible», y lo que no se pudo medir se indica aparte.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence


SHORT = {"Precio": "PRE", "Descuento": "DES", "DTE": "DTE", "Yield": "YLD", "Crédito/ancho": "C/A", "Delta": "DEL", "IV Rank": "IVR", "IV Percentile": "IVP"}


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


def _spread_return(view, min_ror: Optional[float], cw_range: tuple[Optional[float], Optional[float]] = (None, None)) -> Criterion:
    """Crédito/ancho de un spread (el equivalente a su yield; es el `yield_now_pct` que muestra la tabla). Se compara con el mínimo y el
    máximo de crédito/ancho del perfil; sin mínimo propio se usa el retorno sobre riesgo mínimo convertido (r/(100+r)), que da el mismo veredicto."""
    cw = view.yield_now_pct
    lo, hi = cw_range
    if lo is None and min_ror is not None:
        lo = min_ror / (100 + min_ror) * 100
    if lo is None and hi is None:
        return Criterion("Yield", None, "en un spread es sobre el ancho: sin mínimo que comparar")
    if cw is None:
        return Criterion("Crédito/ancho", None, "sin dato")
    limits = " · ".join(t for t in (None if lo is None else f"mín. {lo:.1f} %", None if hi is None else f"máx. {hi:g} %") if t)
    return Criterion("Crédito/ancho", (lo is None or cw >= lo - 1e-9) and (hi is None or cw <= hi + 1e-9), f"{cw:.1f} % ({limits})")


def _fit(name: str, view, *, discount_min: float, dte_min: int, dte_max: Optional[int], yield_min: float,
         delta_min: Optional[float] = None, delta_max: Optional[float] = None,
         iv_rank_min: Optional[float] = None, iv_pct_min: Optional[float] = None,
         price: tuple[Optional[float], Optional[float]] = (None, None), spread_min_ror: Optional[float] = None,
         spread_cw: tuple[Optional[float], Optional[float]] = (None, None)) -> Fit:
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
        out.append(_spread_return(view, spread_min_ror, spread_cw))
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
                   price=(c.min_price, c.max_price), spread_min_ror=c.min_return_on_risk_pct or default_ror if spread else None,
                   spread_cw=(c.min_credit_width_pct, c.max_credit_width_pct) if spread and not other else (None, None))
        if other:                        # el rendimiento no es comparable entre una put (sobre el strike) y un spread (sobre el riesgo)
            fit = replace(fit, criteria=tuple(Criterion(k.label, None, "perfil de " + ("put" if spread else "spread") + ": no comparable")
                                              if k.label in ("Yield", "Crédito/ancho") else k for k in fit.criteria))
        out.append(fit)
    return tuple(out)
