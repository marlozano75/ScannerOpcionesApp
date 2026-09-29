"""Cushion y semáforo de riesgo (RF-15, RF-16)."""
from __future__ import annotations

from typing import Optional

from scanner_opciones.config.settings import CushionThresholds
from scanner_opciones.domain.enums import TrafficLight
from scanner_opciones.domain.models import AccountSummary, RiskMetric, RiskStatus, SeverityStatus


def cushion_pct(excess_liquidity: Optional[float], net_liquidation: Optional[float]) -> Optional[float]:
    """Excess Liquidity / Net Liquidation Value * 100."""
    if excess_liquidity is None or net_liquidation is None or net_liquidation <= 0:
        return None
    return excess_liquidity / net_liquidation * 100


def classify(cushion: Optional[float], t: CushionThresholds) -> TrafficLight:
    """> normal_above: verde; > concern_above: ámbar; resto: rojo. Los límites exactos caen abajo
    (40 -> ámbar, 30 -> rojo). Sin dato: UNKNOWN, nunca verde."""
    if cushion is None:
        return TrafficLight.UNKNOWN
    if cushion > t.normal_above:
        return TrafficLight.GREEN
    if cushion > t.concern_above:
        return TrafficLight.AMBER
    return TrafficLight.RED


def _metric(excess: Optional[float], net_liq: Optional[float], t: CushionThresholds) -> RiskMetric:
    c = cushion_pct(excess, net_liq)
    return RiskMetric(cushion_pct=c, excess_liquidity=excess, light=classify(c, t))


SEVERITY = {
    0: (TrafficLight.GREEN, "Normal"),
    1: (TrafficLight.AMBER, "Advertencia / margen bajo"),
    2: (TrafficLight.ORANGE, "Riesgo elevado / cerca del límite"),
    3: (TrafficLight.RED, "Liquidación / situación de margen crítica"),
}


def classify_severity(level: Optional[int]) -> SeverityStatus:
    """HighestSeverity de IBKR. Sin dato o valor desconocido: UNKNOWN (nunca verde)."""
    if level is None or level not in SEVERITY:
        return SeverityStatus(level, TrafficLight.UNKNOWN, "Sin datos")
    light, label = SEVERITY[level]
    return SeverityStatus(level, light, label)


def build_risk_status(summary: AccountSummary, t: CushionThresholds) -> RiskStatus:
    """Cushion actual = el que da IBKR directamente. Look Ahead y Post-Expiration no tienen
    cushion propio en la API: se calculan como su Excess Liquidity / Net Liquidation."""
    nl = summary.net_liquidation
    current = RiskMetric(summary.cushion_pct, summary.excess_liquidity, classify(summary.cushion_pct, t))
    return RiskStatus(
        current=current,
        look_ahead=_metric(summary.look_ahead_excess, nl, t),
        # IBKR envía 0 cuando no hay vencimientos que evaluar: se trata como "sin datos", no como riesgo
        post_expiration=_metric(summary.post_expiration_excess or None, nl, t),
        severity=classify_severity(summary.highest_severity),
        updated_at=summary.updated_at,
    )
