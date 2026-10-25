"""Historial de resultados (beneficio por acción real frente a la estimación de los analistas) y dividendos de cada empresa, para la ficha de los
gráficos. Solo informa: no filtra ni descarta nada. Los resultados salen de Yahoo (`yahoo.py`: tastytrade solo da el EPS real y la estimación del
último trimestre, no el historial de estimaciones) y los dividendos de las *market metrics* de tastytrade. Funciones puras; sin red."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Protocol, Sequence


class HistoryError(Exception):
    """El proveedor no responde o limita las peticiones: se conserva lo guardado."""


@dataclass(frozen=True)
class EarningsQuarter:
    day: date                            # día en que se presentaron los resultados
    actual: Optional[float] = None       # BPA real
    estimate: Optional[float] = None     # BPA estimado por los analistas antes de presentar

    def to_json(self) -> dict:
        return {"day": self.day.isoformat(), "actual": self.actual, "estimate": self.estimate}

    @classmethod
    def from_json(cls, raw: Mapping) -> "EarningsQuarter":
        return cls(date.fromisoformat(str(raw["day"])[:10]), raw.get("actual"), raw.get("estimate"))

    @property
    def beat(self) -> Optional[bool]:
        """True si el BPA real alcanzó o superó la estimación (None si falta alguno de los dos)."""
        if self.actual is None or self.estimate is None:
            return None
        return round(self.actual, 4) >= round(self.estimate, 4)


@dataclass(frozen=True)
class AnnualYear:
    end: date                      # fin del año fiscal
    revenue: Optional[float] = None       # ingresos, USD
    net_income: Optional[float] = None    # beneficio neto, USD
    source: str = "SEC EDGAR"             # de dónde salen las cifras (Yahoo Finance cuando EDGAR no las tiene)

    def to_json(self) -> dict:
        out = {"end": self.end.isoformat(), "revenue": self.revenue, "net_income": self.net_income}
        if self.source != "SEC EDGAR":
            out["source"] = self.source
        return out

    @classmethod
    def from_json(cls, raw: Mapping) -> "AnnualYear":
        return cls(date.fromisoformat(str(raw["end"])[:10]), raw.get("revenue"), raw.get("net_income"), str(raw.get("source") or "SEC EDGAR"))

    @property
    def net_margin_pct(self) -> Optional[float]:
        """Beneficio neto sobre ingresos, en % (None sin ingresos o sin beneficio)."""
        if self.revenue is None or self.net_income is None or self.revenue <= 0:
            return None
        return self.net_income / self.revenue * 100.0


@dataclass(frozen=True)
class Dividend:
    yield_pct: Optional[float] = None        # rentabilidad por dividendo anual, en %
    per_payment: Optional[float] = None      # $ por acción de cada pago
    ex_date: Optional[date] = None           # último o próximo ex-dividendo
    pay_date: Optional[date] = None          # fecha de pago de ese dividendo

    def to_json(self) -> dict:
        return {"yield_pct": self.yield_pct, "per_payment": self.per_payment,
                "ex_date": self.ex_date.isoformat() if self.ex_date else None,
                "pay_date": self.pay_date.isoformat() if self.pay_date else None}

    @classmethod
    def from_json(cls, raw: Mapping) -> "Dividend":
        def day(key: str) -> Optional[date]:
            return date.fromisoformat(str(raw[key])[:10]) if raw.get(key) else None
        return cls(raw.get("yield_pct"), raw.get("per_payment"), day("ex_date"), day("pay_date"))

    @property
    def empty(self) -> bool:
        return not self.yield_pct and not self.per_payment


def growth_pct(previous: Optional[float], current: Optional[float]) -> Optional[float]:
    """Variación interanual en %: (actual − anterior) / anterior. None si falta alguno o el anterior no es positivo (con una base negativa o nula
    el porcentaje no significa nada)."""
    if previous is None or current is None or previous <= 0:
        return None
    return (current - previous) / previous * 100.0


def annual_growth(years: Sequence["AnnualYear"]) -> list[tuple[Optional[float], Optional[float]]]:
    """(variación de ingresos, variación del beneficio neto) en % de cada año frente al anterior, uno por año: el primero y los que siguen a un
    año que falta (menos de 350 o más de 380 días) salen (None, None)."""
    out: list[tuple[Optional[float], Optional[float]]] = [(None, None)]
    for before, now in zip(years, years[1:]):
        if 350 <= (now.end - before.end).days <= 380:
            out.append((growth_pct(before.revenue, now.revenue), growth_pct(before.net_income, now.net_income)))
        else:
            out.append((None, None))
    return out[:len(years)]


def eps_growth(quarters: Sequence["EarningsQuarter"]) -> Optional[tuple[float, "EarningsQuarter", "EarningsQuarter"]]:
    """Variación interanual del BPA real del último trimestre frente al del mismo trimestre del año anterior (350–380 días antes), con ambos
    trimestres; None si no se puede comparar."""
    if not quarters:
        return None
    last = quarters[-1]
    for q in quarters[:-1]:
        if 350 <= (last.day - q.day).days <= 380:
            g = growth_pct(q.actual, last.actual)
            return None if g is None else (g, q, last)
    return None


def last_quarters(rows: Sequence[EarningsQuarter], n: int) -> list[EarningsQuarter]:
    """Los últimos `n` trimestres con resultado real, del más antiguo al más reciente."""
    done = sorted((r for r in rows if r.actual is not None), key=lambda r: r.day)
    return done[-n:]


def beat_summary(rows: Sequence[EarningsQuarter]) -> Optional[tuple[int, int]]:
    """(trimestres en que se alcanzó o superó la estimación, trimestres comparables); None si ninguno tiene estimación."""
    known = [r.beat for r in rows if r.beat is not None]
    return (sum(known), len(known)) if known else None


class EarningsHistoryProvider(Protocol):
    async def get_earnings_history(self, tickers: list[str], quarters: int) -> dict[str, list[EarningsQuarter]]:
        """Resultados de los últimos trimestres de cada ticker. Lista vacía = consultado y sin historial; los que fallan no aparecen."""


class DividendProvider(Protocol):
    async def get_dividends(self, tickers: list[str]) -> dict[str, Dividend]:
        """Dividendo de cada ticker (vacío si no paga). Los que no se pudieron consultar no aparecen."""


class FakeEarningsHistory:
    """Proveedor en memoria para los tests."""

    def __init__(self, data: Optional[dict[str, list[EarningsQuarter]]] = None) -> None:
        self.data = data or {}
        self.calls: list[tuple] = []

    async def get_earnings_history(self, tickers: list[str], quarters: int) -> dict[str, list[EarningsQuarter]]:
        self.calls.append(("get_earnings_history", tuple(tickers)))
        return {t: self.data[t] for t in tickers if t in self.data}


class FakeDividends:
    def __init__(self, data: Optional[dict[str, Dividend]] = None) -> None:
        self.data = data or {}
        self.calls: list[tuple] = []

    async def get_dividends(self, tickers: list[str]) -> dict[str, Dividend]:
        self.calls.append(("get_dividends", tuple(tickers)))
        return {t: self.data[t] for t in tickers if t in self.data}
