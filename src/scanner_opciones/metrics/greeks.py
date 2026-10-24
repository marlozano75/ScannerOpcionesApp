"""Delta e IV de una put con Black-Scholes (europeo, sin dividendos), para clasificar las puts vendidas del Panel sin pedir cotizaciones.

Es una aproximación (las opciones sobre acciones son americanas y pagan dividendos): sirve para saber si una put está «testeada», en el dinero o muy
dentro, no para fijar precios. Las cotizaciones en vivo de tastytrade traen la delta real y se prefieren cuando se evalúa un roll. Funciones puras."""
from __future__ import annotations

import math
from typing import Optional

MIN_IV, MAX_IV = 0.01, 5.0          # fracción: 1 % - 500 %
PRICE_TOLERANCE = 0.005             # por debajo de este extrínseco ($/acción) el precio no distingue la volatilidad


def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _d1(spot: float, strike: float, years: float, iv: float, rate: float) -> float:
    return (math.log(spot / strike) + (rate + iv * iv / 2) * years) / (iv * math.sqrt(years))


def put_price(spot: float, strike: float, years: float, iv: float, rate: float = 0.0) -> float:
    d1 = _d1(spot, strike, years, iv, rate)
    d2 = d1 - iv * math.sqrt(years)
    return strike * math.exp(-rate * years) * _cdf(-d2) - spot * _cdf(-d1)


def put_delta(spot: float, strike: float, years: float, iv: float, rate: float = 0.0) -> float:
    """Delta de una put, en negativo (−0,30); su valor absoluto es la «delta» de las reglas de defensa."""
    return _cdf(_d1(spot, strike, years, iv, rate)) - 1.0


def implied_vol(price: float, spot: float, strike: float, years: float, rate: float = 0.0) -> Optional[float]:
    """IV (fracción) que da `price` a una put, por bisección. None si el precio no la determina (no supera el valor intrínseco o no cabe en
    1–500 %)."""
    if min(price, spot, strike, years) <= 0:
        return None
    intrinsic = max(strike * math.exp(-rate * years) - spot, 0.0)
    if price <= intrinsic + PRICE_TOLERANCE or price >= strike:
        return None
    lo, hi = MIN_IV, MAX_IV
    if not put_price(spot, strike, years, lo, rate) <= price <= put_price(spot, strike, years, hi, rate):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if put_price(spot, strike, years, mid, rate) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def estimate_abs_delta(spot: float, strike: float, dte: int, price: Optional[float], fallback_iv: Optional[float] = None,
                       rate: float = 0.0) -> Optional[float]:
    """|delta| estimada de una put: con la IV que implica su precio actual (`price`, $/acción) y, si el precio no la determina (extrínseco casi
    nulo), con `fallback_iv` (fracción, p. ej. la IV a 30 días del subyacente). None sin datos para calcularla.
    Con el vencimiento hoy (`dte` 0) se trata como 0,5 días para no dividir por cero."""
    if not spot or spot <= 0 or not strike or strike <= 0:
        return None
    years = max(dte, 0.5) / 365
    iv = implied_vol(price, spot, strike, years, rate) if price else None
    if iv is None:
        iv = fallback_iv if fallback_iv and MIN_IV <= fallback_iv <= MAX_IV else None
    if iv is None:
        return None
    return abs(put_delta(spot, strike, years, iv, rate))
