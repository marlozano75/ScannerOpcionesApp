"""Screener «Call Debit Spread muy OTM» (MTO; análisis en docs/mto-spreads.md).

Funciones puras sobre snapshots de calls guardados: comprada = call lejos del dinero (al ask) y vendida = call `ancho` $ más arriba (precio de
venta de referencia). Filtro obligatorio de rentabilidad mínima esperada: el subyacente debe poder mover lo necesario para ganar
`target_gain_pct` del débito en `move_days` días (movimiento necesario ≤ `max_required_move_mult` × movimiento esperado).
Los filtros de calidad de la empresa los aplica quien llama (`scanner.quality`) antes de pasar los snapshots."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Optional, Sequence

from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.metrics import technical as ta
from scanner_opciones.metrics.yields import mid_price, reference_price

EPS = 1e-6


@dataclass(frozen=True)
class CdsResult:
    long: ContractSnapshot            # call comprada
    short: ContractSnapshot           # call vendida
    width: float
    debit: float                      # por acción (comprada al ask − vendida al precio de referencia + comisiones)
    max_loss: float                   # = débito × multiplicador, en $
    max_return_pct: float             # (ancho − débito) / débito
    breakeven: float
    breakeven_distance_pct: Optional[float]
    long_otm_pct: float
    net_delta: float
    required_move_pct: float          # % del precio que debe subir para ganar el objetivo
    expected_move_pct: float          # movimiento esperado a `move_days` días (%)
    required_move_mult: float         # necesario / esperado (≤ max_required_move_mult)
    sma200_distance_pct: Optional[float]   # % del precio respecto a la SMA200 (negativo = por debajo)
    prob_pct: float = 0.0             # probabilidad (%) de tocar el nivel que da el objetivo en `move_days` días
    score: float = 0.0                # prob × retorno máximo / 100: rendimiento del débito ponderado por la probabilidad (orden de la tabla)


def touch_probability_pct(z: float) -> float:
    """P(el subyacente toca un nivel a z desviaciones) = 2·(1 − Φ(z)) en %, con el principio de reflexión (sin deriva)."""
    return min(100.0, 200 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


def _spread_pct(s: ContractSnapshot) -> Optional[float]:
    """Spread bid/ask en % del mid; None si no es un mercado válido (sin bid, sin ask o ask 0)."""
    if s.bid is None or s.ask is None or s.bid <= 0 or s.ask <= 0 or mid_price(s.bid, s.ask) is None:
        return None
    return (s.ask - s.bid) / mid_price(s.bid, s.ask) * 100


def _leg_ok(s: ContractSnapshot, cds: CdsSettings) -> bool:
    sp = _spread_pct(s)
    return sp is not None and sp <= cds.max_leg_spread_pct + EPS and (s.open_interest or 0) >= cds.min_long_oi


def build_call_debit_spreads(
    snapshots: Sequence[ContractSnapshot], infos: Mapping[str, TickerInfo], cds: CdsSettings, today: date,
    bars: Optional[Mapping[str, Sequence]] = None,
    price_reference: PriceReference = PriceReference.BID_PLUS_SPREAD, price_spread_pct: float = 25.0,
    cost_ps: Callable[[float], float] = lambda multiplier: 0.0,
) -> list[CdsResult]:
    """CDS que cumplen `cds`, ordenados por puntuación (probabilidad de alcanzar el objetivo × retorno máximo), como mucho `top_per_ticker` por ticker. `cost_ps(multiplicador)` = comisiones por
    acción de las dos patas (y del cierre, si procede). Sin delta o sin IV no hay movimiento esperado: el spread se descarta."""
    calls = {(s.contract.ticker, s.contract.expiry, round(s.contract.strike, 4)): s
             for s in snapshots if s.contract.right is OptionRight.CALL}
    out: list[CdsResult] = []
    sma_cache: dict[str, Optional[float]] = {}
    for (ticker, expiry, strike), lg in calls.items():
        info = infos.get(ticker)
        price = info.underlying_price if info else None
        if not price or price <= 0:
            continue
        dte = (expiry - today).days
        otm = (strike / price - 1) * 100
        if not (cds.dte_min <= dte <= cds.dte_max) or not (cds.long_otm_pct_min - EPS <= otm <= cds.long_otm_pct_max + EPS):
            continue
        dist200: Optional[float] = None
        if bars is not None or cds.sma200_below_pct_min is not None:      # la distancia a la SMA200 se calcula siempre que haya cierres (es una columna)
            if ticker not in sma_cache:
                sma_cache[ticker] = ta.sma([px for _, px in (bars or {}).get(ticker, [])], 200)
            if sma_cache[ticker]:
                dist200 = (price / sma_cache[ticker] - 1) * 100
        if cds.sma200_below_pct_min is not None:
            if dist200 is None:
                continue                   # sin 200 sesiones no se puede comprobar el retroceso
            if dist200 > -cds.sma200_below_pct_min + EPS:
                continue
        if not _leg_ok(lg, cds) or lg.delta is None:
            continue
        iv = (info.iv30 / 100) if info and info.iv30 else lg.iv
        if not iv or iv <= 0:
            continue
        expected_pct = iv * math.sqrt(cds.move_days / 365) * 100
        mult = lg.contract.multiplier
        found: list[CdsResult] = []
        for width in cds.width_options:
            width_pct = width / strike * 100
            if (cds.width_pct_min is not None and width_pct < cds.width_pct_min - EPS) or (cds.width_pct_max is not None and width_pct > cds.width_pct_max + EPS):
                continue
            sh = calls.get((ticker, expiry, round(strike + width, 4)))
            if sh is None or sh.delta is None or sh.contract.multiplier != mult or not _leg_ok(sh, cds):
                continue
            sell = reference_price(sh.bid, sh.ask, price_reference, price_spread_pct)
            if sell is None:
                continue
            debit = lg.ask - sell + cost_ps(mult)
            if debit <= EPS or debit >= width:
                continue
            if not (cds.min_debit_usd - EPS <= debit * mult <= cds.max_debit_usd + EPS):
                continue
            max_return = (width - debit) / debit * 100
            if cds.min_max_return_pct is not None and max_return < cds.min_max_return_pct - EPS:
                continue
            net_delta = abs(lg.delta) - abs(sh.delta)
            if net_delta <= EPS:
                continue                   # sin delta neta positiva una subida no da el objetivo
            required_pct = cds.target_gain_pct / 100 * debit / net_delta / price * 100
            required_mult = required_pct / expected_pct
            if required_mult > cds.max_required_move_mult + EPS:
                continue                   # filtro obligatorio: rentabilidad mínima esperada
            prob = touch_probability_pct(required_mult)
            if cds.min_prob_pct is not None and prob < cds.min_prob_pct - EPS:
                continue                   # probabilidad mínima de alcanzar el objetivo
            breakeven = strike + debit
            found.append(CdsResult(lg, sh, width, debit, debit * mult, max_return, breakeven, (breakeven / price - 1) * 100,
                                   otm, net_delta, required_pct, expected_pct, required_mult, dist200, prob, prob * max_return / 100))
        if cds.best_width and found:
            found = [max(found, key=lambda r: r.score)]
        out.extend(found)
    out.sort(key=lambda r: -r.score)
    if cds.top_per_ticker:
        seen: dict[str, int] = {}
        kept: list[CdsResult] = []
        for r in out:                      # ya ordenado: los primeros de cada ticker son los de mayor puntuación
            n = seen.get(r.long.contract.ticker, 0)
            if n < cds.top_per_ticker:
                seen[r.long.contract.ticker] = n + 1
                kept.append(r)
        out = kept
    return out
