"""Screener «Call Debit Spread muy OTM» (MTO; análisis en docs/mto-spreads.md).

Funciones puras sobre snapshots de calls guardados: comprada = call lejos del dinero (al ask) y vendida = call `ancho` $ más arriba (precio de
venta de referencia). Filtro obligatorio de rentabilidad mínima esperada: el subyacente debe poder mover lo necesario para ganar
`target_gain_pct` del débito en `move_days` días (movimiento necesario ≤ `max_required_move_mult` × movimiento esperado).
Los filtros de calidad de la empresa los aplica quien llama (`scanner.quality`) antes de pasar los snapshots."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
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
    score: float = 0.0                # prob × retorno neto de salida % / 100 × √(ganancia neta de salida en $ por contrato): rendimiento ponderado por la probabilidad y por la ganancia absoluta (orden de la tabla)
    exit_value: float = 0.0           # valor del spread en la salida (por acción) = débito × (1 + objetivo)
    exit_credit_usd: float = 0.0      # lo que se cobra al cerrar en el objetivo, en $ por contrato, ya sin la comisión de cierre
    exit_profit_usd: float = 0.0      # ganancia neta en $ por contrato (cobrado − débito)
    exit_return_pct: float = 0.0      # ganancia neta / débito, en % (el objetivo menos la comisión de cierre)


def touch_probability_pct(z: float) -> float:
    """P(el subyacente toca un nivel a z desviaciones) = 2·(1 − Φ(z)) en %, con el principio de reflexión (sin deriva)."""
    return min(100.0, 200 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


def iv_at_horizon(info: Optional[TickerInfo], days: int, today: date) -> Optional[float]:
    """IV (fracción) para un horizonte de `days` días: interpola linealmente en varianza total (IV²·T) entre los dos vencimientos
    de `info.expiry_ivs` que lo rodean; con uno solo a un lado usa el más cercano; sin vencimientos usa el IV30 (None si tampoco hay)."""
    pts: list[tuple[int, float]] = []
    for iso, iv in ((info.expiry_ivs if info else None) or {}).items():
        try:
            d = (date.fromisoformat(iso) - today).days
        except ValueError:
            continue
        if d >= 1 and iv and iv > 0:
            pts.append((d, float(iv)))
    pts.sort()
    if pts:
        below = [p for p in pts if p[0] <= days]
        above = [p for p in pts if p[0] >= days]
        if below and above:
            (d1, v1), (d2, v2) = below[-1], above[0]
            if d1 == d2:
                return v1
            w = v1 * v1 * d1 + (v2 * v2 * d2 - v1 * v1 * d1) * (days - d1) / (d2 - d1)
            return math.sqrt(w / days)
        return (below[-1] if below else above[0])[1]
    return (info.iv30 / 100) if info and info.iv30 else None


def earnings_in_window(info: Optional[TickerInfo], days: int, today: date) -> bool:
    """True si la próxima presentación de resultados cae entre hoy y dentro de `days` días (una fecha ya pasada es la última, no cuenta)."""
    e = info.next_earnings if info else None
    return e is not None and today <= e <= today + timedelta(days=days)


def _spread_pct(s: ContractSnapshot) -> Optional[float]:
    """Spread bid/ask en % del mid; None si no es un mercado válido (sin bid, sin ask o ask 0)."""
    if s.bid is None or s.ask is None or s.bid <= 0 or s.ask <= 0 or mid_price(s.bid, s.ask) is None:
        return None
    return (s.ask - s.bid) / mid_price(s.bid, s.ask) * 100


def _size_ok(size: Optional[int], minimum: Optional[int]) -> bool:
    return minimum is None or (size is not None and size >= minimum)


def _leg_ok(s: ContractSnapshot, cds: CdsSettings, long: bool) -> bool:
    """Liquidez de una pata: spread bid/ask, OI, y tamaño del bid y del ask si se exigen (la comprada y la vendida tienen límites propios de spread y OI)."""
    sp = _spread_pct(s)
    max_sp, min_oi = (cds.max_long_spread_pct, cds.min_long_oi) if long else (cds.max_short_spread_pct, cds.min_short_oi)
    return (sp is not None and sp <= max_sp + EPS and (s.open_interest or 0) >= min_oi
            and _size_ok(s.bid_size, cds.min_bid_size) and _size_ok(s.ask_size, cds.min_ask_size))


def build_call_debit_spreads(
    snapshots: Sequence[ContractSnapshot], infos: Mapping[str, TickerInfo], cds: CdsSettings, today: date,
    bars: Optional[Mapping[str, Sequence]] = None,
    price_reference: PriceReference = PriceReference.BID_PLUS_SPREAD, price_spread_pct: float = 25.0,
    cost_ps: Callable[[float], float] = lambda multiplier: 0.0,
) -> list[CdsResult]:
    """CDS que cumplen `cds`, ordenados por puntuación (probabilidad × retorno de salida % × √ganancia de salida en $), como mucho `top_per_ticker` por ticker. `cost_ps(multiplicador)` = comisiones por
    acción de las dos patas (y del cierre, si procede). Sin delta o sin IV no hay movimiento esperado: el spread se descarta."""
    calls = {(s.contract.ticker, s.contract.expiry, round(s.contract.strike, 4)): s
             for s in snapshots if s.contract.right is OptionRight.CALL}
    strikes: dict[tuple, list[float]] = {}         # solo con `all_widths`: strikes guardados de cada (ticker, vencimiento)
    if cds.all_widths:
        for t, e, k in calls:
            strikes.setdefault((t, e), []).append(k)
        for ks in strikes.values():
            ks.sort()
    out: list[CdsResult] = []
    sma_cache: dict[str, Optional[float]] = {}
    for (ticker, expiry, strike), lg in calls.items():
        info = infos.get(ticker)
        price = info.underlying_price if info else None
        if not price or price <= 0:
            continue
        if cds.exclude_earnings and earnings_in_window(info, cds.move_days, today):
            continue                       # resultados dentro de la ventana del movimiento esperado
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
        if not _leg_ok(lg, cds, True) or lg.delta is None:
            continue
        iv = iv_at_horizon(info, cds.move_days, today) or lg.iv
        if not iv or iv <= 0:
            continue
        expected_pct = iv * math.sqrt(cds.move_days / 365) * 100
        mult = lg.contract.multiplier
        found: list[CdsResult] = []
        for width in ([round(k - strike, 4) for k in strikes[(ticker, expiry)] if k > strike + EPS] if cds.all_widths else cds.width_options):
            width_pct = width / strike * 100
            if (cds.width_pct_min is not None and width_pct < cds.width_pct_min - EPS) or (cds.width_pct_max is not None and width_pct > cds.width_pct_max + EPS):
                continue
            sh = calls.get((ticker, expiry, round(strike + width, 4)))
            if sh is None or sh.delta is None or sh.contract.multiplier != mult or not _leg_ok(sh, cds, False):
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
            exit_value = debit * (1 + cds.target_gain_pct / 100)
            if exit_value >= width - EPS:
                continue                   # el objetivo supera lo que puede valer el spread
            close_cost = cost_ps(mult)
            exit_profit = exit_value - close_cost - debit
            exit_return = exit_profit / debit * 100
            if cds.min_exit_profit_usd is not None and exit_profit * mult < cds.min_exit_profit_usd - EPS:
                continue                   # ganancia de salida mínima en $
            prob = touch_probability_pct(required_mult)
            if cds.min_prob_pct is not None and prob < cds.min_prob_pct - EPS:
                continue                   # probabilidad mínima de alcanzar el objetivo
            breakeven = strike + debit
            found.append(CdsResult(lg, sh, width, debit, debit * mult, max_return, breakeven, (breakeven / price - 1) * 100,
                                   otm, net_delta, required_pct, expected_pct, required_mult, dist200, prob, prob * exit_return / 100 * math.sqrt(max(exit_profit * mult, 0.0)),
                                   exit_value, (exit_value - close_cost) * mult, exit_profit * mult, exit_return))
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
