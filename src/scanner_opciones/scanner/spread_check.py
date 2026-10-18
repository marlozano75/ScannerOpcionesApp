"""Revisión de los spreads para cada put vendida que pasa el escaneo como put sola: por qué cada una sale o no como spread.

Parte de las puts que cumplen TODOS los filtros en el escenario «put vendida» (con su yield mínimo) y, para cada una, evalúa
cada condición del spread (existe la pata comprada, mercado de las dos patas, OI, crédito, retorno, pérdida máxima…) con su
valor y si cumple. Si hay varios anchos se muestra el más cercano a salir (con pata comprada, menos condiciones fallidas y, a igualdad, mayor
retorno sobre riesgo). Funciones puras sobre los snapshots; las condiciones replican las de `spreads.build_spreads`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot
from scanner_opciones.metrics.yields import mid_price
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.spreads import combo_spread_pct

EPS = 1e-6

# (clave, cabecera)
COLUMNS: tuple[tuple[str, str], ...] = (
    ("long", "Pata comprada"),
    ("long_oi", "OI comprada"),
    ("long_delta", "Δ comprada"),
    ("long_spread", "Spread comprada (%)"),
    ("credit", "Crédito ($/contrato)"),
    ("cw", "Crédito / ancho (%)"),
    ("combo", "Spread combo / crédito (%)"),
    ("ror", "Retorno/riesgo (%)"),
    ("loss", "Pérdida máx. (% NLV)"),
)


@dataclass(frozen=True)
class Check:
    text: str                    # valor que se muestra
    ok: Optional[bool]           # True cumple, False no cumple, None = sin condición activa o sin dato


@dataclass(frozen=True)
class CheckRow:
    ticker: str
    expiry: object
    dte: int
    strike: float
    width: Optional[float]       # ancho evaluado (None: no hay pata comprada a ningún ancho)
    checks: dict                 # clave de COLUMNS -> Check
    failed: tuple[str, ...]      # cabeceras de las condiciones que no cumple
    outcome: str                 # put | spread | no
    detail: str                  # texto del resultado


def _pct(x: float) -> str:
    return f"{x:.1f}"


def _spread_of(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (ask - bid) / mid_price(bid, ask) * 100


def _cmp(value: Optional[float], limit: Optional[float], text: str, upper: bool) -> Check:
    """`upper`: el valor tiene que ser ≤ límite; si no, ≥ límite."""
    if limit is None:
        return Check(text, None)
    if value is None:
        return Check(text, False)
    return Check(text, value <= limit + EPS if upper else value >= limit - EPS)


def _evaluate(r, long: Optional[ContractSnapshot], width: float, criteria: ScanCriteria, nlv_usd: Optional[float]) -> tuple[dict, float]:
    """Condiciones del spread con su valor, y su retorno sobre riesgo (−1e9 si no se puede calcular; sirve para elegir el ancho)."""
    s = r.snapshot
    sc = s.contract
    checks: dict[str, Check] = {}
    if long is None or long.ask is None or long.ask <= 0 or mid_price(long.bid, long.ask) is None:
        checks["long"] = Check(f"{sc.strike - width:g} · no existe" if long is None else f"{long.contract.strike:g} · sin ask", False)
        for key in ("long_oi", "long_delta", "long_spread", "credit", "cw", "combo", "ror", "loss"):
            checks[key] = Check("—", None)
        return checks, -1e9
    checks["long"] = Check(f"{long.contract.strike:g}", True)
    checks["long_oi"] = _cmp(float(long.open_interest or 0), criteria.min_long_oi, str(long.open_interest if long.open_interest is not None else "—"), False)
    delta = abs(long.delta) if long.delta is not None else None
    checks["long_delta"] = _cmp(delta, criteria.max_long_abs_delta, f"{delta:.2f}" if delta is not None else "sin dato", True)
    long_spread = _spread_of(long.bid, long.ask)
    checks["long_spread"] = _cmp(long_spread, criteria.max_long_spread_pct, _pct(long_spread) if long_spread is not None else "sin bid", True)
    sell = r.reference_price
    if sell is None:
        for key in ("credit", "cw", "combo", "ror", "loss"):
            checks[key] = Check("—", False)
        return checks, -1e9
    credit = sell - long.ask
    max_loss_ps = width - credit
    if credit <= EPS or max_loss_ps <= EPS:
        checks["credit"] = Check(f"{credit * sc.multiplier:.0f}", False)
        checks["cw"] = Check("—", False)
        checks["combo"] = Check("—", None)
        checks["ror"] = Check("—", False)
        checks["loss"] = Check("—", None)
        return checks, -1e9
    checks["credit"] = _cmp(credit * sc.multiplier, criteria.min_spread_credit, f"{credit * sc.multiplier:.0f}", False)
    cw = credit / width * 100
    low, high = _cmp(cw, criteria.min_credit_width_pct, _pct(cw), False), _cmp(cw, criteria.max_credit_width_pct, _pct(cw), True)
    checks["cw"] = Check(_pct(cw), None if low.ok is None and high.ok is None else (low.ok is not False and high.ok is not False))
    combo = combo_spread_pct(s, long, credit)
    checks["combo"] = _cmp(combo, criteria.max_combo_spread_pct, _pct(combo) if combo != float("inf") else "—", True)
    ror = credit / max_loss_ps * 100
    checks["ror"] = _cmp(ror, criteria.min_return_on_risk_pct, _pct(ror), False)
    loss_pct = max_loss_ps * sc.multiplier / nlv_usd * 100 if nlv_usd else None
    checks["loss"] = (_cmp(loss_pct, criteria.max_loss_nlv_pct, _pct(loss_pct), True) if loss_pct is not None
                      else Check("sin NLV", None))
    return checks, ror


def spread_check(
    shorts: Sequence, snapshots: Sequence[ContractSnapshot], criteria: ScanCriteria, nlv_usd: Optional[float] = None,
) -> list[CheckRow]:
    """Una fila por put vendida de `shorts` (las que pasan como put sola) con la evaluación de su mejor ancho."""
    index = {(s.contract.ticker, s.contract.expiry, round(s.contract.strike, 4)): s
             for s in snapshots if s.contract.right is OptionRight.PUT}
    headers = dict(COLUMNS)
    rows: list[tuple[tuple, CheckRow]] = []
    for r in shorts:
        sc = r.snapshot.contract
        best: Optional[tuple[tuple, float, dict]] = None
        for width in criteria.spread_widths:
            long = index.get((sc.ticker, sc.expiry, round(sc.strike - width, 4)))
            checks, ror = _evaluate(r, long, width, criteria, nlv_usd)
            failed = sum(1 for c in checks.values() if c.ok is False)
            key = (long is None, failed, -ror)   # primero los anchos con pata comprada (se pueden evaluar del todo)
            if best is None or key < best[0]:
                best = (key, width, checks)
        if best is None:
            continue
        key, width, checks = best
        failed = tuple(headers[k] for k, c in checks.items() if c.ok is False)
        naked_fits = criteria.mixed and criteria.max_naked_strike is not None and sc.strike <= criteria.max_naked_strike + EPS
        if criteria.mixed and (criteria.max_naked_strike is None or naked_fits):
            outcome, detail = "put", f"Sale como put (strike ≤ {criteria.max_naked_strike:g})" if criteria.max_naked_strike is not None else "Sale como put"
        elif not failed:
            outcome, detail = "spread", "Sale como spread" + (f" (strike > {criteria.max_naked_strike:g})" if criteria.mixed else "")
        else:
            outcome, detail = "no", "No sale: " + ", ".join(failed)
        order = (0 if outcome != "no" else 1, len(failed), key[2])
        rows.append((order, CheckRow(sc.ticker, sc.expiry, r.dte, sc.strike, width, checks, failed, outcome, detail)))
    rows.sort(key=lambda x: (x[0], x[1].ticker, x[1].expiry, -x[1].strike))
    return [row for _, row in rows]
