"""Reconoce con qué estrategia (perfil del Scanner) se abrió una put vendida o un spread que aparece en la cartera.

IBKR no dice con qué estrategia se abrió una posición. Se reconstruye lo que se sabía EL DÍA DE LA APERTURA y se compara con los filtros de
cada perfil: descuento del strike (con el cierre del subyacente ese día), DTE al abrir, yield del periodo de la prima cobrada (put) o retorno
sobre riesgo del periodo (spread). Lo que no se puede reconstruir (delta, IV Rank, técnicos, calidad) no se evalúa. Funciones puras.

Elección: 1) los perfiles que cumplen TODO lo medible; entre ellos gana el más exigente (mayor descuento mínimo) y, a igualdad, el de rango de
DTE más estrecho; 2) si ninguno lo cumple del todo, el que tiene el DTE de apertura en su rango y más criterios cumple (aproximado); 3) si ni
el DTE encaja en ninguno, el perfil de recambio («Tortuga»).

`recognize_shown` es el paso previo cuando el registro de escaneos cubre el día de la apertura: solo compite entre los perfiles que mostraban el contrato."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.scanner.profiles import Profile


@dataclass(frozen=True)
class EntryFacts:
    kind: str                           # put | spread
    dte: int                            # días al vencimiento el día de la apertura
    discount_pct: Optional[float]       # % del strike vendido por debajo del cierre del subyacente ese día
    yield_pct: Optional[float] = None   # put: prima / strike, sin anualizar
    ror_pct: Optional[float] = None     # spread: crédito / (ancho − crédito), sin anualizar


@dataclass(frozen=True)
class EntryFit:
    profile: str
    passed: int
    total: int
    exact: bool                         # cumple todo lo medible
    note: str                           # texto para el Panel

    def to_json(self) -> dict:
        return {"profile": self.profile, "passed": self.passed, "total": self.total, "exact": self.exact, "note": self.note}

    @classmethod
    def from_json(cls, raw: dict) -> "EntryFit":
        return cls(str(raw["profile"]), int(raw["passed"]), int(raw["total"]), bool(raw.get("exact")), str(raw.get("note", "")))


def _num(params: dict, key: str) -> Optional[float]:
    try:
        return float(str(params[key]).replace(",", "."))
    except (KeyError, ValueError):
        return None


def _criteria(facts: EntryFacts, params: dict) -> list[tuple[str, Optional[bool], str]]:
    """(criterio, cumple, texto): None = no medible."""
    out: list[tuple[str, Optional[bool], str]] = []
    lo = _num(params, "discount")
    if lo is not None:
        d = facts.discount_pct
        out.append(("DES", None if d is None else d >= lo - 1e-9, "sin cierre del día" if d is None else f"descuento {d:.1f} % (mín. {lo:g} %)"))
    dmin, dmax = _num(params, "dte_min"), _num(params, "dte_max")
    if dmin is not None or dmax is not None:
        ok = (dmin is None or facts.dte >= dmin) and (dmax is None or facts.dte <= dmax)
        out.append(("DTE", ok, f"{facts.dte} DTE (rango {'' if dmin is None else f'{dmin:g}'}–{'' if dmax is None else f'{dmax:g}'})"))
    if facts.kind == "put":
        y = _num(params, "min_yield")
        if y is not None:
            v = facts.yield_pct
            out.append(("YLD", None if v is None else v >= y - 1e-9, "sin prima" if v is None else f"yield {v:.2f} % (mín. {y:g} %)"))
    elif str(params.get("use_rr", "")).lower() in ("on", "1", "true"):
        r = _num(params, "rr")
        if r is not None:
            v = facts.ror_pct
            out.append(("RR", None if v is None else v >= r - 1e-9, "sin crédito" if v is None else f"retorno/riesgo {v:.1f} % (mín. {r:g} %)"))
    return out


def recognize(facts: EntryFacts, profiles: Sequence[Profile], fallback: str) -> EntryFit:
    scored = []
    for order, p in enumerate(profiles):
        if p.name == fallback or facts.kind not in p.instruments:
            continue
        params = p.set_params(facts.kind)
        crit = _criteria(facts, params)
        known = [c for c in crit if c[1] is not None]
        if not crit or not any(c[0] == "DTE" for c in crit):
            continue            # sin rango de DTE el perfil no sirve para reconocer
        passed = sum(1 for c in known if c[1])
        dmin, dmax = _num(params, "dte_min"), _num(params, "dte_max")
        width = (dmax - dmin) if dmin is not None and dmax is not None else 10_000
        dte_ok = next(c[1] for c in crit if c[0] == "DTE")
        scored.append(dict(p=p, order=order, crit=crit, passed=passed, known=len(known), exact=all(c[1] for c in known) and bool(known) and dte_ok,
                           dte_ok=dte_ok, disc=_num(params, "discount") or 0.0, width=width))
    exact = [s for s in scored if s["exact"]]
    if exact:
        best = min(exact, key=lambda s: (-s["disc"], s["width"], s["order"]))
        return EntryFit(best["p"].name, best["passed"], best["known"], True, _note(best, "cumple lo medible"))
    near = [s for s in scored if s["dte_ok"]]
    if near:
        best = min(near, key=lambda s: (-(s["passed"] / max(s["known"], 1)), s["width"], s["order"]))
        return EntryFit(best["p"].name, best["passed"], best["known"], False, _note(best, "aproximado: no cumple todo"))
    dte = f"{facts.dte} DTE al abrir"
    return EntryFit(fallback, 0, 0, False, f"{dte}: no encaja en el rango de ningún perfil")


def recognize_shown(facts: EntryFacts, profiles: Sequence[Profile], shown: Sequence[str], day: date) -> Optional[EntryFit]:
    """Perfil entre `shown` (los que mostraban el contrato el día `day` en el registro de escaneos). Uno solo: ese. Varios: desempate por los filtros
    actuales (cumplir todo lo medible, luego más criterios cumplidos) y, a igualdad, el de rango de DTE más estrecho (el más restrictivo), luego el de mayor
    descuento mínimo y el primero de la lista. None si ninguno de `shown` sirve para este instrumento (el llamante usa entonces los filtros)."""
    scored = []
    for order, p in enumerate(profiles):
        if p.name not in shown or facts.kind not in p.instruments:
            continue
        params = p.set_params(facts.kind)
        crit = _criteria(facts, params)
        known = [c for c in crit if c[1] is not None]
        dmin, dmax = _num(params, "dte_min"), _num(params, "dte_max")
        width = (dmax - dmin) if dmin is not None and dmax is not None else 10_000
        passed = sum(1 for c in known if c[1])
        scored.append(dict(p=p, order=order, crit=crit, passed=passed, known=len(known), width=width, disc=_num(params, "discount") or 0.0,
                           exact=bool(known) and passed == len(known), fallback=p.fallback))
    if len(scored) > 1:
        scored = [s for s in scored if not s["fallback"]] or scored      # el perfil de recambio acepta de todo: solo cuenta si es el único
    if not scored:
        return None
    best = min(scored, key=lambda s: (not s["exact"], -(s["passed"] / max(s["known"], 1)), s["width"], -s["disc"], s["order"]))
    names = ", ".join(s["p"].name for s in scored)
    if len(scored) == 1:
        note = f"Reconocida por el registro de escaneos: aparecía en {names} el {day:%d/%m}."
    else:
        note = (f"Reconocida por el registro de escaneos: aparecía en {names} el {day:%d/%m}; desempate por filtros y, a igualdad, el rango de DTE más estrecho: "
                + _note(best, "cumple lo medible" if best["exact"] else "aproximado").split(": ", 1)[1])
    return EntryFit(best["p"].name, best["passed"], best["known"], best["exact"], note)


def _note(s: dict, verdict: str) -> str:
    marks = " · ".join(f"{'✓' if ok else '✗' if ok is False else '?'} {text}" for _, ok, text in s["crit"])
    return f"Reconocida por los filtros al abrir ({verdict}): {marks}"
