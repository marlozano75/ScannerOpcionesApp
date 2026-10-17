"""Alertas de presentaciones ante la SEC (8-K y avisos de retraso) que suelen preceder a un problema serio de la empresa:
cuentas que ya no son fiables, cambio de auditor, riesgo de exclusión de bolsa, quiebra o impago de deuda.

La app nunca decide con ellas: solo marca el ticker (⚠) para que lo mires antes de vender una put. Hay falsos positivos (un cambio de
auditor rutinario) pero casi ningún caso grave pasa sin una de estas presentaciones. Funciones puras; la descarga está en `edgar.py`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Protocol, Sequence

# ítem del 8-K -> qué significa
ITEMS = {
    "1.03": "Quiebra o suspensión de pagos",
    "2.04": "Hechos que aceleran o agravan obligaciones de deuda",
    "3.01": "Aviso de exclusión de cotización o de incumplimiento de sus requisitos",
    "4.01": "Cambio de auditor",
    "4.02": "Las cuentas ya publicadas no son fiables (reformulación)",
}
LATE_FORMS = {"NT 10-K": "Retraso en presentar el informe anual", "NT 10-Q": "Retraso en presentar el informe trimestral"}


@dataclass(frozen=True)
class FilingAlert:
    day: date
    form: str           # «8-K», «NT 10-K»...
    item: str           # ítem del 8-K («4.02») o «» en los demás
    label: str

    @property
    def text(self) -> str:
        code = f"{self.form} {self.item}".strip()
        return f"{self.day.strftime('%d/%m/%Y')} · {self.label} ({code})"

    def to_json(self) -> dict:
        return {"day": self.day.isoformat(), "form": self.form, "item": self.item, "label": self.label}

    @classmethod
    def from_json(cls, raw: Mapping) -> "FilingAlert":
        return cls(date.fromisoformat(raw["day"]), raw["form"], raw.get("item", ""), raw["label"])


def classify_filings(recent: Mapping[str, Sequence], today: date, window_days: int) -> list[FilingAlert]:
    """Alertas de la sección `filings.recent` de la API de la SEC (listas paralelas `form`, `filingDate`, `items`) de los últimos
    `window_days` días, las más recientes primero. Una fila mal formada se ignora."""
    forms, days, items = recent.get("form", ()), recent.get("filingDate", ()), recent.get("items", ())
    out: list[FilingAlert] = []
    for k, form in enumerate(forms):
        try:
            day = date.fromisoformat(str(days[k]))
        except (IndexError, ValueError):
            continue
        if not 0 <= (today - day).days <= window_days:
            continue
        if form.startswith("8-K"):
            raw = str(items[k]) if k < len(items) else ""
            for item in (i.strip() for i in raw.split(",")):
                if item in ITEMS:
                    out.append(FilingAlert(day, "8-K", item, ITEMS[item]))
        elif form in LATE_FORMS:
            out.append(FilingAlert(day, form, "", LATE_FORMS[form]))
    return sorted(out, key=lambda a: a.day, reverse=True)


class FilingAlertsProvider(Protocol):
    async def get_filing_alerts(self, tickers: list[str], window_days: int) -> dict[str, list[FilingAlert]]:
        """Un resultado por ticker consultado con éxito (lista vacía = sin alertas); los que el proveedor no conoce
        se devuelven también vacíos. Lanza `FinancialsError` si la SEC falla o bloquea."""
        ...


@dataclass
class FakeFilingAlerts:
    data: dict[str, list[FilingAlert]] = field(default_factory=dict)
    error: Optional[Exception] = None
    calls: list[tuple] = field(default_factory=list)

    async def get_filing_alerts(self, tickers: list[str], window_days: int) -> dict[str, list[FilingAlert]]:
        self.calls.append(("get_filing_alerts", tuple(tickers)))
        if self.error is not None:
            raise self.error
        return {t: list(self.data.get(t, [])) for t in tickers}
