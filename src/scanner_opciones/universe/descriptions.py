"""Explicación de los criterios con los que cada fuente del Universo selecciona sus acciones.

Texto fijo en español, tomado de lo que publican las propias fuentes: la descripción de cada estrategia de HelloStocks
(página de estrategias) y el resumen de la metodología del RS Score de RankedStocks. Funciones puras, sin red.
"""
from __future__ import annotations

from dataclasses import dataclass

from scanner_opciones.universe.sources import RANKED


@dataclass(frozen=True)
class SourceInfo:
    title: str
    summary: str                         # qué busca la fuente
    details: tuple[str, ...] = ()        # puntos adicionales, lo más escuetos posible


HELLO_NOTE = ("Universo: las 500 mayores acciones de EE. UU. por capitalización. Una acción cumple la estrategia solo si pasa "
              "todos sus criterios (columna Criteria, p. ej. 7/7); los cumplimientos parciales son solo pistas. "
              "Los umbrales exactos no vienen en la página guardada.")

# Nombre de la fuente (el de la pestaña del libro / estrategia) -> qué busca.
_HELLO = {
    "Lower Risk (Hello Stocks)": "Finanzas sólidas, buen crecimiento y precio bajo.",
    "Balanced Risk (Hello Stocks)": "Finanzas sólidas, buen crecimiento y precio entre justo y bajo.",
    "Full Throttle (Hello Stocks)": "Crecimiento muy alto a un precio justo.",
    "Value Investing-Warren Buffett": "Grandes negocios a un precio entre justo y bajo.",
    "Defensive Investing-Benjamin G": "Acciones a un precio bajo.",
    "GrowthReasonablePrice_P.Lynch": "Mezcla de crecimiento y valor.",
    "Growth Inv. Philip Fisher": "Crecimiento enorme a un precio todavía razonable.",
    "Magic Formula Joel Greenblatt": "Rentabilidad por beneficio y retorno sobre el capital.",
    "Contrarian Inv David Drem": "Acciones a contracorriente con alto potencial.",
    "Price SalesRatioFocus KenFisher": "Da importancia al ratio precio/ventas.",
    "Deep Value Inv. Seth Klarman": "Empresas que cotizan por debajo de su valor intrínseco.",
}

RANKED_INFO = SourceInfo(
    "RankedStocks · RS Score",
    "Ranking por el RS Score (0–100), que resume cinco factores frente a empresas comparables.",
    (
        "Factores: valoración, crecimiento, rentabilidad, sentimiento (cómo ha cotizado hace poco) y perspectiva "
        "(cambio en las estimaciones de analistas). Los pesos son privados; horizonte de unos 12 meses.",
        "Bandas: Elite 80–100 · Strong 60–79 · Neutral 40–59 · Weak 0–39. Se exigen los cuatro primeros factores.",
        "Se recalcula cada día antes de la apertura de EE. UU. Es una herramienta de investigación, no una recomendación.",
    ),
)


def describe(name: str, columns: tuple[str, ...] = ()) -> SourceInfo | None:
    """Explicación de la fuente `name`; `columns` son sus columnas, que en HelloStocks son las métricas evaluadas."""
    if name == RANKED:
        return RANKED_INFO
    if name in _HELLO:
        measured = [c for c in columns if c.strip().lower() not in {"ticker", "fuente", "company", "sector", "criteria"}]
        details = (HELLO_NOTE,) + ((f"Métricas evaluadas: {', '.join(measured)}.",) if measured else ())
        return SourceInfo(f"HelloStocks · {name}", _HELLO[name], details)
    return None


def describe_manual(name: str) -> SourceInfo:
    return SourceInfo(name, "Lista propia: tickers escritos a mano, sin criterio de selección.")
