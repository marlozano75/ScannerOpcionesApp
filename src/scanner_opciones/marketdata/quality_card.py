"""Línea de «calidad» de la ficha de cada empresa con lo que da tastytrade (sus *market metrics*, tabla `ticker_quality`): beneficio por acción de 12 meses,
trimestres con beneficios, capitalización, liquidez de las opciones, próximos resultados y sorpresa del último BPA. Solo informa; funciones puras."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Optional

from scanner_opciones.domain.models import TickerInfo


def _num(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _cap(value: float) -> str:
    for limit, unit in ((1e12, "B"), (1e9, "mil M"), (1e6, "M")):
        if value >= limit:
            return f"{_num(value / limit, 1)} {unit} $"
    return f"{_num(value, 0)} $"


def quality_parts(info: Optional[TickerInfo], today: Optional[date] = None) -> list[tuple[str, str]]:
    """[(etiqueta, valor)] de lo que tastytrade informa; vacío si no hay nada. Con `today`, los resultados llevan los días que faltan."""
    if info is None:
        return []
    parts: list[tuple[str, str]] = []
    if info.eps_ttm is not None:
        parts.append(("BPA 12 m", f"{_num(info.eps_ttm)} $"))
    if info.positive_quarters is not None and info.reported_quarters:
        parts.append(("Trimestres con beneficios", f"{info.positive_quarters} de {info.reported_quarters}"))
    if info.market_cap:
        parts.append(("Capitalización", _cap(info.market_cap)))
    if info.option_liquidity is not None:
        parts.append(("Liquidez de opciones", f"{info.option_liquidity}/5"))
    if info.next_earnings is not None and (today is None or info.next_earnings >= today):
        days = f" (en {(info.next_earnings - today).days} d)" if today is not None else ""
        parts.append(("Próximos resultados", info.next_earnings.strftime("%d/%m/%Y") + days))
    if info.eps_surprise_pct is not None:
        parts.append(("Último BPA frente al consenso", f"{'+' if info.eps_surprise_pct > 0 else ''}{_num(info.eps_surprise_pct, 1)} %"))
    return parts


def quality_text(info: Optional[TickerInfo]) -> str:
    """Línea de texto para el tooltip del ticker (sin días restantes: se guarda en memoria entre peticiones)."""
    parts = quality_parts(info)
    return "Calidad (tastytrade): " + " · ".join(f"{label} {value}" for label, value in parts) if parts else ""


def quality_html(info: Optional[TickerInfo], today: date) -> str:
    """Fragmento HTML para la ficha de los gráficos; vacío si tastytrade no dio nada."""
    parts = quality_parts(info, today)
    if not parts:
        return ""
    bits = " · ".join(f'<span class="muted">{escape(label)}</span> <b>{escape(value)}</b>' for label, value in parts)
    return (f'<div class="co-quality" style="margin:0 0 8px;font-size:12.5px;line-height:1.4"><b>Calidad</b> '
            f'<span class="muted">(tastytrade)</span> · {bits}</div>')
