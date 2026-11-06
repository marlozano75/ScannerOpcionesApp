"""Rentabilidad REAL a ~30 días de las puts de un ticker en los últimos vencimientos mensuales (informe, no filtra nada). Funciones puras.

Para cada tercer viernes ya vencido se toma el día en que faltaban `dte` días y, con el precio del subyacente de ese día, los strikes que están al descuento
mínimo del perfil o por debajo (los más cercanos de la rejilla habitual de strikes). El bid de cierre de cada uno entre su strike es el yield del periodo; de
los strikes de un vencimiento cuenta el mejor. Un ticker cuyo mejor yield no llega al exigido en suficientes meses «apenas da prima».

Si el perfil exige spread máximo u OI mínimo, un strike solo cuenta si su ask y OI de ese día (velas `price=ask` y `price=last`) los cumplen.

Límites conocidos: la rejilla de strikes es una suposición (un strike que no existe no tiene velas y solo se pierde esa opción); el bid de cierre de un contrato
sin mercado es nulo; con pocos meses con dato no se decide nada; el spread sale de dos cierres diarios (puede no ser simultáneo) y el OI/IV de la última vela
con operaciones (hasta 3 días antes); el tamaño del bid no existe en las velas."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Optional, Sequence

from scanner_opciones.metrics.spread import spread_pct

OK, DISCARD, NO_DATA, PENDING = "ok", "descartable", "sin dato", "sin pedir"
DAY_TOLERANCE = 3        # días hacia atrás en los que se acepta el bid si el del día de referencia no tiene vela


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def past_monthly_expiries(today: date, months: int) -> list[date]:
    """Los últimos `months` terceros viernes YA VENCIDOS (anteriores a `today`), del más antiguo al más reciente."""
    out: list[date] = []
    year, month = today.year, today.month
    while len(out) < months:
        e = third_friday(year, month)
        if e < today:
            out.append(e)
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out)


def reference_day(expiry: date, dte: int, days: Sequence[date]) -> Optional[date]:
    """Último día con cierre que no pasa de `expiry − dte`; None si no hay."""
    limit = expiry - timedelta(days=dte)
    ok = [d for d in days if d <= limit]
    return max(ok) if ok else None


def strike_steps(price: float) -> tuple[float, ...]:
    return (0.5, 1.0, 2.5) if price < 50 else (1.0, 2.5, 5.0) if price < 200 else (5.0, 10.0)


def candidate_strikes(price: float, discount_pct: float) -> list[float]:
    """Strikes de la rejilla habitual iguales o inferiores a `precio × (1 − descuento)`, el más cercano de cada paso, de mayor a menor."""
    target = price * (1 - discount_pct / 100)
    found = {round(math.floor(target / s) * s, 4) for s in strike_steps(price)}
    return sorted((k for k in found if k > 0), reverse=True)


def dx_root(ticker: str) -> Optional[str]:
    """Raíz del símbolo dxFeed; None para clases de acciones y símbolos raros (no se piden)."""
    t = ticker.strip().upper()
    return t if t.isalnum() else None


def option_symbol(root: str, expiry: date, strike: float) -> str:
    return f".{root}{expiry.strftime('%y%m%d')}P{strike:g}"


def pick_bid(series: Mapping[date, float], ref_day: date, tolerance: int = DAY_TOLERANCE) -> Optional[float]:
    """Bid del día de referencia o, si no hay vela ese día, el del día anterior más cercano dentro de `tolerance` días."""
    for back in range(tolerance + 1):
        v = series.get(ref_day - timedelta(days=back))
        if v is not None and v > 0:
            return v
    return None


def pick_last(series: Mapping[date, tuple], ref_day: date, tolerance: int = DAY_TOLERANCE) -> Optional[tuple]:
    """(OI, IV, volumen) del día de referencia o, si ese día no hubo operaciones, del anterior más cercano dentro de `tolerance` días."""
    for back in range(tolerance + 1):
        v = series.get(ref_day - timedelta(days=back))
        if v is not None:
            return v
    return None


def strike_yield(strike: float, row: Optional[tuple], max_spread_pct: Optional[float], min_oi: Optional[int]) -> tuple[Optional[float], bool, bool]:
    """(yield %, pendiente, descartado por liquidez) de un strike. `row` = (bid, ask, OI, IV, volumen, detalle pedido) de `BidHistoryRepo.all_details`; None = aún no
    pedido. Con `max_spread_pct` / `min_oi` (los del perfil) un strike cuenta solo si su spread de cierre y su OI los cumplen; como en el Scanner, un dato que
    falta no pasa. Sin esos filtros solo importa el bid. Si faltan ask/OI/IV por pedir y hay filtros, el strike queda pendiente."""
    if row is None:
        return None, True, False
    bid, ask, oi, _iv, _vol, detail = row
    if not bid:
        return None, False, False
    if max_spread_pct is None and min_oi is None:
        return bid / strike * 100, False, False
    if not detail:
        return None, True, False
    if max_spread_pct is not None:
        spread = spread_pct(bid, ask)
        if spread is None or spread > max_spread_pct:
            return None, False, True
    if min_oi is not None and (oi is None or oi < min_oi):
        return None, False, True
    return bid / strike * 100, False, False


@dataclass(frozen=True)
class Month:
    expiry: date
    ref_day: date
    price: float
    best_pct: Optional[float]        # mejor yield entre los strikes con bid (y liquidez, si el perfil la exige); None = ninguno
    pending: bool = False            # algún strike aún no se ha pedido a tastytrade
    illiquid: int = 0                # strikes con bid descartados por spread u OI del perfil


@dataclass(frozen=True)
class Verdict:
    ticker: str
    status: str
    months: tuple[Month, ...]
    hits: int                        # meses que llegan al yield exigido
    with_data: int                   # meses con algún bid

    @property
    def best_pct(self) -> Optional[float]:
        values = [m.best_pct for m in self.months if m.best_pct is not None]
        return max(values) if values else None


def judge(ticker: str, months: Sequence[Month], required_pct: float, min_hits: int, min_months_with_data: int) -> Verdict:
    """DESCARTABLE si hay al menos `min_months_with_data` meses con bid y menos de `min_hits` llegan al yield exigido. Sin esos meses con dato no se decide
    (sin dato); con strikes por pedir, tampoco (sin pedir)."""
    hits = sum(1 for m in months if m.best_pct is not None and m.best_pct >= required_pct)
    with_data = sum(1 for m in months if m.best_pct is not None)
    if hits >= min_hits:
        status = OK
    elif any(m.pending for m in months):
        status = PENDING
    elif with_data < min_months_with_data:
        status = NO_DATA
    else:
        status = DISCARD
    return Verdict(ticker, status, tuple(months), hits, with_data)
