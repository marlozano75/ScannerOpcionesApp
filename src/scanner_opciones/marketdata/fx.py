"""Tipos de cambio diarios a dólares, para convertir los importes de las empresas que no reportan en USD (ADR de Brasil, Taiwán, Europa…).
Los cocientes (pasivo/patrimonio, ROIC, margen…) no necesitan conversión: solo los importes absolutos (flujo de caja libre, ingresos, beneficio)."""
from __future__ import annotations

import bisect
from datetime import date, timedelta
import logging
from typing import Callable, Iterable, Mapping, Optional, Protocol

MAX_GAP_DAYS = 10     # sin cotización en los 10 días anteriores a la fecha pedida (festivos, fines de semana), no se inventa el cambio


class FxError(Exception):
    pass


log = logging.getLogger(__name__)
RateAt = Callable[[str, date], Optional[float]]


class FxProvider(Protocol):
    async def get_history(self, currency: str) -> Mapping[date, float]:
        """Dólares por unidad de `currency`, por día. Lanza `FxError` si no se puede obtener."""
        ...


def rate_on(history: Mapping[date, float], day: date) -> Optional[float]:
    """Cambio del día `day` o, si ese día no cotiza, el último anterior (hasta `MAX_GAP_DAYS` antes); None si no hay."""
    if not history:
        return None
    days = sorted(history)
    i = bisect.bisect_right(days, day) - 1
    if i < 0 or day - days[i] > timedelta(days=MAX_GAP_DAYS):
        return None
    return history[days[i]]


class RateBook:
    """Cambios diarios a dólares de cada moneda, pedidos una sola vez por moneda al proveedor `fx` y guardados en memoria."""

    def __init__(self, fx: Optional[FxProvider]) -> None:
        self._fx = fx
        self._history: dict[str, Mapping[date, float]] = {}

    async def prepare(self, currencies: Iterable[str]) -> Optional[RateAt]:
        """Función `(moneda, día) -> cambio` para las monedas pedidas, o None si no hay proveedor o no se pudo obtener ninguna."""
        currencies = set(currencies)
        for currency in currencies - set(self._history):
            if self._fx is None:
                break
            try:
                self._history[currency] = dict(await self._fx.get_history(currency))
            except FxError as exc:
                log.warning("Sin tipo de cambio %s/USD; los importes de esas empresas quedan sin dato: %s", currency, exc)
        if not any(c in self._history for c in currencies):
            return None
        return lambda currency, day: rate_on(self._history.get(currency, {}), day)


class FakeFx:
    """Proveedor en memoria para los tests: `rates` = {moneda: tipo fijo o {fecha: tipo}}."""

    def __init__(self, rates: Optional[dict] = None, error: Optional[Exception] = None) -> None:
        self.rates, self.error, self.calls = rates or {}, error, []

    async def get_history(self, currency: str) -> Mapping[date, float]:
        self.calls.append(currency)
        if self.error is not None:
            raise self.error
        value = self.rates.get(currency)
        if value is None:
            raise FxError(f"sin cambio para {currency}")
        return value if isinstance(value, Mapping) else {date(2000, 1, 1) + timedelta(days=n): float(value) for n in range(0, 12000, 1)}
