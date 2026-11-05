"""Selección natural de tickers (F2, docs/seleccion-tickers.md): estado ACTIVO / TEMPLADO / CUARENTENA según la prima de contratos que se pueden recomprar,
observada durante varias sesiones (funciones puras). Nunca decide con una sola foto: hacen falta `min_days` sesiones con medida; antes, el ticker es ACTIVO.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from scanner_opciones.config.settings import SelectionSettings
from scanner_opciones.scanner.premium import CDS_PROFILE

ACTIVE, WARM, QUARANTINE = "active", "warm", "quarantine"
NOT_CLOSABLE_FACTOR = 0.8      # una prima que cumple pero sin contratos cerrables nunca es un acierto: cuenta como el 80 % (a lo sumo, templado)


@dataclass(frozen=True)
class ProfileTarget:
    """Lo que un perfil exige a la prima: yield mínimo del periodo y DTE medio de su ventana (para comparar el yield anualizado de la banda ancha)."""
    min_yield_pct: float
    dte_mid: float


def day_ratio(rows: Iterable[dict], targets: dict[str, ProfileTarget], allowed: Optional[set[str]] = None) -> Optional[float]:
    """Cuánto de lo exigido cumple el ticker ese día: 1 = cumple con un contrato cerrable. El mejor perfil permitido manda. None = sin medida utilizable.
    - Perfil de put: mejor yield de contratos cerrables / yield mínimo; sin cerrables, el mejor yield × 0,8; sin contratos en la ventana (calendario de
      vencimientos), el yield anualizado de la banda ancha frente al exigido × 0,8.
    - CDS: 1 si hay calls cerrables en su ventana; 0,5 si hay calls pero ninguna cerrable."""
    best: Optional[float] = None
    for r in rows:
        name = r["profile"]
        if allowed is not None and name not in allowed:
            continue
        if name == CDS_PROFILE:
            if CDS_PROFILE not in targets:
                continue
            ratio = 1.0 if r["closable"] > 0 else (0.5 if r["contracts"] > 0 else None)
        else:
            target = targets.get(name)
            if target is None or target.min_yield_pct <= 0:
                continue
            if r["best_closable_yield_pct"] is not None:
                ratio = r["best_closable_yield_pct"] / target.min_yield_pct
            elif r["best_yield_pct"] is not None:
                ratio = NOT_CLOSABLE_FACTOR * min(r["best_yield_pct"] / target.min_yield_pct, 1.0)
            elif r["best_ann_wide_pct"] is not None and target.dte_mid > 0:
                ratio = NOT_CLOSABLE_FACTOR * min(r["best_ann_wide_pct"] / (target.min_yield_pct * 365 / target.dte_mid), 1.0)
            else:
                ratio = None
        if ratio is not None and (best is None or ratio > best):
            best = ratio
    return best


def evaluate(ratios: list[float], rules: SelectionSettings, previous: Optional[str] = None) -> tuple[str, str]:
    """(estado, motivo) con los `ratios` diarios (cronológicos, solo sesiones con medida). Se usan las últimas `window_days`.
    ACTIVO: ≥ `min_hits` sesiones con ratio ≥ 1 (uno menos si ya era activo: histéresis). TEMPLADO: ≥ `min_hits` con ratio ≥ `warm_ratio`. Si no, CUARENTENA."""
    recent = ratios[-rules.window_days:]
    if len(recent) < rules.min_days:
        return ACTIVE, f"sin historial suficiente ({len(recent)} de {rules.min_days} sesiones con medida)"
    hits = sum(1 for x in recent if x >= 1.0)
    warm = sum(1 for x in recent if x >= rules.warm_ratio)
    need = max(1, rules.min_hits - 1) if previous == ACTIVE else rules.min_hits
    if hits >= need:
        return ACTIVE, f"cumple en {hits} de las últimas {len(recent)} sesiones"
    if warm >= rules.min_hits:
        return WARM, f"cumple en {hits} y casi en {warm - hits} más de las últimas {len(recent)} sesiones (≥ {rules.warm_ratio:.0%} de lo exigido)"
    return QUARANTINE, f"prima lejos de cumplir: {warm} de las últimas {len(recent)} sesiones llegan al {rules.warm_ratio:.0%} de lo exigido (mín. {rules.min_hits})"
