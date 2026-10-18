"""Cambios de valoración de analistas (subidas y bajadas de rating y de precio objetivo) de los últimos días.

La app nunca decide con ellos: solo marca el ticker (▼ si hubo alguna bajada de rating, ▲ si solo hubo subidas) para que lo mires antes de
vender una put. Funciones puras; la descarga está en `yahoo.py` (Yahoo Finance, gratuita y no oficial)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Protocol, Sequence

UP, DOWN, INIT, SAME = "up", "down", "init", "main"     # `Action` de Yahoo: «reit» (reitera) se trata como SAME


class AnalystError(Exception):
    """Yahoo no responde o limita las peticiones: se conserva lo guardado."""


@dataclass(frozen=True)
class AnalystChange:
    day: date
    firm: str
    action: str                       # up | down | init | main
    from_grade: str
    to_grade: str
    target_from: Optional[float] = None
    target_to: Optional[float] = None

    @property
    def target_moved(self) -> int:
        """+1 si subió el precio objetivo, −1 si lo bajó, 0 si no cambió o falta alguno de los dos."""
        if not self.target_from or not self.target_to:
            return 0
        return (self.target_to > self.target_from) - (self.target_to < self.target_from)

    @property
    def text(self) -> str:
        grade = self.to_grade or "—"
        if self.action in (UP, DOWN) and self.from_grade:
            grade = f"{self.from_grade} → {grade}"
        verb = {UP: "sube a", DOWN: "baja a", INIT: "inicia en"}.get(self.action, "mantiene")
        out = f"{self.day.strftime('%d/%m/%Y')} · {self.firm}: {verb} {grade}"
        if self.target_to:
            out += f" · objetivo {_money(self.target_to)}"
            if self.target_from and self.target_from != self.target_to:
                out += f" (antes {_money(self.target_from)})"
        return out

    def to_json(self) -> dict:
        return {"day": self.day.isoformat(), "firm": self.firm, "action": self.action, "from": self.from_grade, "to": self.to_grade,
                "pt_from": self.target_from, "pt_to": self.target_to}

    @classmethod
    def from_json(cls, raw: Mapping) -> "AnalystChange":
        return cls(date.fromisoformat(raw["day"]), raw["firm"], raw["action"], raw.get("from", ""), raw.get("to", ""),
                   raw.get("pt_from"), raw.get("pt_to"))


def _money(v: Optional[float]) -> str:
    return f"{v:,.2f} $" if v is not None else "—"


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None      # NaN y 0 de Yahoo = sin dato


def classify_changes(rows: Sequence[Mapping], today: date, window_days: int) -> list[AnalystChange]:
    """Cambios de los últimos `window_days` días, los más recientes primero. `rows`: filas con `day`, `firm`, `action`, `from`, `to`,
    `pt_from`, `pt_to` (ya normalizadas por el proveedor). Una fila mal formada se ignora; las que solo reiteran sin mover el precio
    objetivo no aportan nada y se descartan."""
    out: list[AnalystChange] = []
    for r in rows:
        try:
            day = r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"])[:10])
        except (KeyError, ValueError):
            continue
        if not 0 <= (today - day).days <= window_days:
            continue
        action = str(r.get("action") or SAME).lower()
        action = action if action in (UP, DOWN, INIT) else SAME
        change = AnalystChange(day, str(r.get("firm") or "?"), action, str(r.get("from") or ""), str(r.get("to") or ""),
                               _num(r.get("pt_from")), _num(r.get("pt_to")))
        if action == SAME and change.target_moved == 0:
            continue
        out.append(change)
    return sorted(out, key=lambda c: c.day, reverse=True)


def summary(changes: Sequence[AnalystChange]) -> tuple[str, str, str]:
    """(símbolo, clase CSS, texto breve) del ticker: ▼ con alguna bajada de rating, ▲ si solo hay subidas o inicios de cobertura; si solo
    se movió el precio objetivo, ▽ (recortes) o △ (subidas)."""
    down = sum(1 for c in changes if c.action == DOWN)
    up = sum(1 for c in changes if c.action in (UP, INIT))
    cuts = sum(1 for c in changes if c.target_moved < 0)
    if down:
        return "▼", "an-dn", f"{down} bajada{'s' if down != 1 else ''} de rating"
    if up:
        return "▲", "an-up", f"{up} subida{'s' if up != 1 else ''} o inicio{'s' if up != 1 else ''} de cobertura"
    if cuts:
        return "▽", "an-dn", "recortes de precio objetivo"
    return "△", "an-up", "subidas de precio objetivo"


class AnalystProvider(Protocol):
    async def get_analyst_changes(self, tickers: list[str], window_days: int) -> dict[str, list[AnalystChange]]:
        """Cambios recientes por ticker (lista vacía = consultado y sin cambios). Lo que no se pudo consultar no aparece. Lanza `AnalystError`
        si el proveedor deja de responder."""
        ...


@dataclass
class FakeAnalysts:
    data: dict[str, list[AnalystChange]] = field(default_factory=dict)
    calls: list = field(default_factory=list)
    error: Optional[Exception] = None

    async def get_analyst_changes(self, tickers: list[str], window_days: int) -> dict[str, list[AnalystChange]]:
        self.calls.append(("get_analyst_changes", tuple(tickers)))
        if self.error is not None:
            raise self.error
        return {t: self.data.get(t, []) for t in tickers}
