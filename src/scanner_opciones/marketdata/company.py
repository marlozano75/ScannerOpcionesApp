"""Ficha informativa de cada empresa para el tooltip del ticker: a qué se dedica y noticias recientes que puedan afectar a su desempeño.

Solo informa: no filtra ni descarta nada. La descripción sale de Yahoo (`yahoo.py`), los titulares de Finnhub (`finnhub.py`) y se guardan con su
fecha (tabla `company_notes`). Los titulares se filtran aquí por palabras clave (resultados, rebajas, demandas, deuda, directivos...) porque la API
devuelve cientos por ticker, casi todos ruido. Además se puede guardar a mano un resumen en español y una lista de noticias revisadas (`curated`),
que mandan sobre lo automático. Funciones puras; sin red."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Protocol, Sequence


class CompanyNotesError(Exception):
    """El proveedor no responde, limita las peticiones o rechaza la clave: se conserva lo guardado."""


@dataclass(frozen=True)
class NewsItem:
    day: date
    headline: str
    source: str = ""
    url: str = ""
    related: int = 0                   # nº de tickers a los que se asocia el titular (0 = desconocido); muchos = resumen de mercado

    def to_json(self) -> dict:
        return {"day": self.day.isoformat(), "headline": self.headline, "source": self.source, "url": self.url}

    @classmethod
    def from_json(cls, raw: Mapping) -> "NewsItem":
        return cls(date.fromisoformat(str(raw["day"])[:10]), str(raw.get("headline") or raw.get("text") or ""),
                   str(raw.get("source") or ""), str(raw.get("url") or ""))


@dataclass(frozen=True)
class CompanyNotes:
    description: str = ""              # automática (Yahoo, en inglés)
    description_at: Optional[date] = None
    news: tuple[NewsItem, ...] = ()    # automáticas (Finnhub, ya filtradas)
    news_at: Optional[date] = None
    summary: str = ""                  # resumen en español guardado a mano: manda sobre `description`
    curated: tuple[NewsItem, ...] = ()  # noticias revisadas a mano: mandan sobre `news`
    curated_at: Optional[date] = None


def short_description(text: str, max_chars: int) -> str:
    """Las primeras frases completas que caben en `max_chars`; si ni la primera cabe, se corta con «…»."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(out) + len(sentence) + 1 > max_chars:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:max_chars - 1].rstrip() + "…"


def _norm(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()[:60]


_NAME_NOISE = re.compile(r"\b(?:inc|corp|corporation|company|co|ltd|plc|holdings?|group|technologies|the|class [a-c]|common stock)\b", re.I)
_GENERIC_FIRST = {"american", "united", "first", "general", "national", "global", "international", "advanced", "applied", "western", "southern",
                  "northern", "pacific", "atlantic", "digital", "capital", "energy", "financial", "bancorp"}


def mention_pattern(ticker: str, name: str = "") -> "re.Pattern[str]":
    """Cómo reconocer en un titular que habla de ESTA empresa: el ticker en mayúsculas como palabra suelta, su nombre sin «Inc./Corp.» o la primera
    palabra del nombre si no es genérica («Robinhood» de «Robinhood Markets»; «American» de «American Express» no vale). Finnhub asocia a cada
    ticker titulares de otras empresas (HOOD recibía los de American Express y MarketAxess), y su campo `related` solo repite el ticker pedido."""
    parts = []
    if len(ticker) >= 2:
        parts.append(r"\b" + re.escape(ticker.upper()) + r"\b")
    clean = re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", re.sub(r"[,.]", " ", name or ""))).strip()
    names = [clean] if clean else []
    first = clean.split(" ")[0] if clean else ""
    if len(first) >= 5 and first.lower() not in _GENERIC_FIRST and first != clean:
        names.append(first)
    if names:
        parts.append("(?i:" + "|".join(r"\b" + re.escape(n) + r"\b" for n in names) + ")")
    return re.compile("|".join(parts) if parts else r"(?!)")


def select_news(items: Sequence[NewsItem], today: date, window_days: int, max_items: int,
                keywords: Sequence[str], exclude: Sequence[str], max_related: int = 3,
                mention: "Optional[re.Pattern[str]]" = None) -> list[NewsItem]:
    """Los titulares de los últimos `window_days` días que mencionan alguna palabra clave y ninguna de `exclude` (clickbait), sin duplicados y
    sin resúmenes de mercado (asociados a más de `max_related` tickers), los más recientes primero y como mucho `max_items`. Con `mention` solo
    valen los que nombran a la empresa (ver `mention_pattern`)."""
    if not keywords:
        return []
    want = re.compile(r"\b(?:" + "|".join(re.escape(k.lower()) for k in keywords) + ")")
    skip = re.compile("|".join(re.escape(k.lower()) for k in exclude)) if exclude else None     # las exclusiones son frases: subcadena
    seen: set[str] = set()
    out: list[NewsItem] = []
    for item in sorted(items, key=lambda n: n.day, reverse=True):
        if not 0 <= (today - item.day).days <= window_days:
            continue
        low = item.headline.lower()
        if not want.search(low) or (skip and skip.search(low)) or (item.related > max_related):
            continue
        if mention is not None and not mention.search(item.headline):
            continue
        key = _norm(item.headline)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def shown_news(notes: CompanyNotes) -> tuple[tuple[NewsItem, ...], Optional[date]]:
    """Las noticias que se enseñan (las revisadas a mano mandan sobre las automáticas) y de cuándo son."""
    return (notes.curated, notes.curated_at) if notes.curated else (notes.news, notes.news_at)


def card_html(name: str, notes: Optional[CompanyNotes], desc_chars: int) -> str:
    """Ficha para los gráficos: nombre completo, a qué se dedica y noticias con fecha (fragmento HTML; todo el texto se escapa)."""
    from html import escape
    description = short_description((notes.summary or notes.description) if notes else "", desc_chars)
    news, stamp = shown_news(notes) if notes else ((), None)
    if not (name or description or news):
        return ""
    out = ['<div class="co-card" style="margin:0 0 8px;font-size:12.5px;line-height:1.4">']
    if name:
        out.append(f'<div><b>{escape(name)}</b></div>')
    if description:
        out.append(f'<div class="muted">{escape(description)}</div>')
    if news:
        out.append(f'<div style="margin-top:4px"><b>Noticias{" revisadas" if notes.curated else ""}</b> '
                   f'<span class="muted">(actualizadas el {stamp.strftime("%d/%m/%Y") if stamp else "?"})</span></div>'
                   '<ul style="margin:2px 0 0;padding-left:18px">')
        for n in news:
            src = f' <span class="muted">({escape(n.source)})</span>' if n.source else ""
            out.append(f'<li><span class="muted">{n.day.strftime("%d/%m")}</span> {escape(n.headline)}{src}</li>')
        out.append("</ul>")
    out.append("</div>")
    return "".join(out)


def tooltip(notes: CompanyNotes, desc_chars: int, news_chars: int = 120) -> str:
    """Texto del tooltip: descripción (la guardada a mano si existe) y, debajo, las noticias con su fecha y de cuándo son."""
    lines: list[str] = []
    description = short_description(notes.summary or notes.description, desc_chars)
    if description:
        lines.append(description)
    news, stamp = shown_news(notes)
    if news:
        lines.append(f"Noticias{' revisadas' if notes.curated else ''} (actualizadas el {stamp.strftime('%d/%m/%Y') if stamp else '?'}):")
        for n in news:
            head = n.headline if len(n.headline) <= news_chars else n.headline[:news_chars - 1].rstrip() + "…"
            lines.append(f"· {n.day.strftime('%d/%m')} {head}{f' ({n.source})' if n.source else ''}")
    return "\n".join(lines)


class DescriptionProvider(Protocol):
    async def get_descriptions(self, tickers: list[str]) -> dict[str, str]:
        """Descripción del negocio por ticker ('' = consultado y sin descripción). Lo que no se pudo consultar no aparece."""
        ...


class NewsProvider(Protocol):
    async def get_news(self, tickers: list[str], since: date, until: date) -> dict[str, list[NewsItem]]:
        """Titulares sin filtrar por ticker (lista vacía = consultado y sin titulares). Lanza `CompanyNotesError` si hay que parar la pasada."""
        ...


@dataclass
class FakeCompanyData:
    descriptions: dict[str, str] = field(default_factory=dict)
    news: dict[str, list[NewsItem]] = field(default_factory=dict)
    calls: list = field(default_factory=list)
    error: Optional[Exception] = None

    async def get_descriptions(self, tickers: list[str]) -> dict[str, str]:
        self.calls.append(("get_descriptions", tuple(tickers)))
        return {t: self.descriptions[t] for t in tickers if t in self.descriptions}

    async def get_news(self, tickers: list[str], since: date, until: date) -> dict[str, list[NewsItem]]:
        self.calls.append(("get_news", tuple(tickers)))
        if self.error is not None:
            raise self.error
        return {t: self.news.get(t, []) for t in tickers}
