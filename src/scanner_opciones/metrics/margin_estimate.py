"""Estimación del margen de una put desnuda sin pedir un what-if a TWS.

Fórmula de referencia Reg T: (prima + máx(20 % del precio − importe fuera del dinero, 10 % del strike)) × multiplicador. Sola se desvía de
IBKR una mediana del 22,6 % (estudio del 2026-11-03, `docs/rendimiento-peticiones.md`); multiplicada por el cociente real/fórmula del propio
ticker (estable entre sus contratos) baja a ~10,6 %. Sirve para ordenar y filtrar el Scanner; el margen real solo se pide para la cartera."""
from __future__ import annotations

import json
from statistics import median
from typing import Iterable, Optional

from scanner_opciones.domain.models import ContractSnapshot, OptionRight


def premium_of(snap: ContractSnapshot) -> Optional[float]:
    """Prima por acción con la que se calcula el margen: mid, o el bid si no hay ask."""
    if snap.bid is not None and snap.ask is not None and snap.ask > 0:
        return (snap.bid + snap.ask) / 2
    return snap.bid


def reg_t_formula(price: float, strike: float, premium: float, multiplier: int = 100) -> float:
    """Margen de referencia de una put desnuda vendida (un contrato)."""
    otm = max(0.0, price - strike)
    return (premium + max(0.2 * price - otm, 0.1 * strike)) * multiplier


def estimate(snap: ContractSnapshot, price: Optional[float], ratio: float = 1.0) -> Optional[float]:
    """Margen estimado (un contrato) o None si faltan precio o prima."""
    premium = premium_of(snap)
    if not price or price <= 0 or premium is None or premium < 0:
        return None
    return reg_t_formula(price, snap.contract.strike, premium, snap.contract.multiplier) * ratio


class MarginRatios:
    """Cociente real/fórmula por ticker (mediana de sus márgenes what-if guardados) y global, para calibrar la fórmula."""

    def __init__(self, per_ticker: dict[str, float], overall: float) -> None:
        self.per_ticker = per_ticker
        self.overall = overall

    def for_ticker(self, ticker: str) -> float:
        return self.per_ticker.get(ticker, self.overall)

    def to_json(self) -> str:
        return json.dumps({"overall": self.overall, "per_ticker": self.per_ticker}, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str, default: float = 1.0) -> "MarginRatios":
        try:
            data = json.loads(raw or "{}")
            return cls({str(t): float(r) for t, r in data.get("per_ticker", {}).items()}, float(data.get("overall", default)))
        except (ValueError, TypeError, AttributeError):
            return cls({}, default)

    def updated_with(self, fresh: "MarginRatios") -> "MarginRatios":
        """Los cocientes de `fresh` (calculados con márgenes reales) mandan; los tickers sin muestras nuevas conservan el que tenían:
        los márgenes reales se van sustituyendo por estimaciones y, sin esto, la calibración se perdería."""
        return MarginRatios({**self.per_ticker, **fresh.per_ticker}, fresh.overall if fresh.per_ticker else self.overall)

    @classmethod
    def calibrate(cls, snaps: Iterable[ContractSnapshot], default: float = 1.0, min_ratio: float = 0.3, max_ratio: float = 10.0) -> "MarginRatios":
        """Solo cuentan los márgenes REALES (`margin_at` con fecha; las estimaciones se guardan sin ella) con el precio al que se pidieron."""
        by_ticker: dict[str, list[float]] = {}
        for s in snaps:
            if s.contract.right is not OptionRight.PUT or s.initial_margin is None or s.margin_at is None or not s.margin_price:
                continue
            base = estimate(s, s.margin_price)
            if base:
                by_ticker.setdefault(s.contract.ticker, []).append(min(max(s.initial_margin / base, min_ratio), max_ratio))
        per = {t: median(v) for t, v in by_ticker.items()}
        every = [r for v in by_ticker.values() for r in v]
        return cls(per, median(every) if every else default)
