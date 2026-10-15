"""Señales de entrada de la estrategia de venta de puts: RSI rebotando, Bollinger %B y cercanía a la media de 50 o a un soporte
(IV/HV y el cambio de IV son filtros obligatorios, no señales).

El texto de la estrategia dice que no hace falta que se cumplan todas: con una o dos basta. Con `ScanCriteria.signals_required = N` > 0, estos
filtros dejan de ser exigencias individuales y el contrato pasa si cumple al menos N de las que estén marcadas (con menos marcadas que N, todas).
Con 0 siguen siendo filtros normales, todos obligatorios.
"""
from __future__ import annotations

from typing import Optional

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import ScanCriteria

# campos del criterio que son señales (neutro = desactivado)
SIGNAL_FIELDS = ("rsi_rebound_max", "max_bollinger_pctb", "near_level_pct")


def signals_reject(criteria: ScanCriteria, info: Optional[TickerInfo], tech, ticker: str, price: Optional[float]) -> Optional[str]:
    """Motivo por el que el contrato no cumple las señales exigidas, o None."""
    n = criteria.signals_required
    if n <= 0:
        return None
    results: list[tuple[str, bool]] = []
    # una señal sin dato no se puede evaluar: ni suma ni resta (como si no estuviera marcada)
    if tech is not None and price is not None:
        results.extend(tech.signal_results(ticker, price))
    if not results:
        return None
    need = min(n, len(results))
    ok = [name for name, passed in results if passed]
    if len(ok) >= need:
        return None
    failed = [name for name, passed in results if not passed]
    return f"solo {len(ok)} de las {need} señales de entrada exigidas (cumple: {', '.join(ok) or 'ninguna'}; falla: {', '.join(failed)})"
