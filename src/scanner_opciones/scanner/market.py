"""Aviso sobre el VIX para la estrategia de venta de puts. NO es un filtro del scanner: el VIX es del mercado, no del contrato.

Se muestra en el Panel junto al gráfico del VIX. Función pura sobre `VixData` y los umbrales de `scanner.strategy`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scanner_opciones.config.settings import StrategySettings
from scanner_opciones.domain.models import VixData


@dataclass(frozen=True)
class VixAdvice:
    level: str    # clase del aviso: ok | info | warn
    text: str


def vix_is_falling(vix: VixData) -> Optional[bool]:
    """¿El VIX actual está por debajo de la media de sus 3 últimos cierres (bajando tras un pico)? None sin datos."""
    closes = [px for _, px in vix.last_closes[-3:]]
    if vix.current is None or len(closes) < 3:
        return None
    return vix.current < sum(closes) / len(closes)


def vix_advice(vix: Optional[VixData], cfg: StrategySettings) -> Optional[VixAdvice]:
    if vix is None or vix.current is None:
        return None
    v, falling = vix.current, vix_is_falling(vix)
    trend = "" if falling is None else (" y bajando" if falling else " y subiendo")
    if v < cfg.min_vix:
        return VixAdvice("warn", f"VIX {v:.1f} (< {cfg.min_vix:g}): primas pobres para vender puts.")
    if v > cfg.max_vix:
        return VixAdvice("warn", f"VIX {v:.1f} (> {cfg.max_vix:g}){trend}: mercado muy nervioso, las primas son altas pero el riesgo de más caídas también; "
                                 "conviene reducir el tamaño y esperar a que se calme.")
    if v >= cfg.ideal_vix_min and falling:
        return VixAdvice("ok", f"VIX {v:.1f} entre {cfg.ideal_vix_min:g} y {cfg.max_vix:g} y bajando tras un pico: condiciones ideales para vender puts.")
    return VixAdvice("info", f"VIX {v:.1f}{trend}: primas aceptables para vender puts (lo ideal es {cfg.ideal_vix_min:g}–{cfg.max_vix:g} y bajando).")
