"""Criterios de escaneo (Regular / Táctica) construidos desde la configuración."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OperationType


@dataclass(frozen=True)
class ScanCriteria:
    operation: OperationType
    strike_below_pct_min: float
    strike_below_pct_max: float
    min_yield_pct: float
    dte_min: int
    dte_max: int
    min_oi: Optional[int] = None
    max_spread_pct: Optional[float] = None
    min_iv_rank: Optional[float] = None
    min_iv_percentile: Optional[float] = None

    def with_filters(self, **overrides) -> "ScanCriteria":
        """Copia con filtros modificados (p. ej. desde la UI)."""
        return replace(self, **overrides)


def criteria_from_settings(settings: Settings, operation: OperationType) -> ScanCriteria:
    """Valores iniciales de una operación. `strike_below_pct` es el descuento MÍNIMO del strike;
    el máximo es el límite de lo guardado (`scanner.candidates`)."""
    f = settings.scanner.filters
    cand = settings.scanner.candidates
    op = settings.scanner.regular if operation is OperationType.REGULAR else settings.scanner.tactical
    return ScanCriteria(
        operation,
        strike_below_pct_min=op.strike_below_pct,
        strike_below_pct_max=cand.strike_below_pct_max,
        min_yield_pct=op.min_yield_pct,
        dte_min=op.dte_min,
        dte_max=op.dte_max,
        min_oi=f.min_oi, max_spread_pct=f.max_spread_pct,
        min_iv_rank=f.min_iv_rank, min_iv_percentile=f.min_iv_percentile,
    )
