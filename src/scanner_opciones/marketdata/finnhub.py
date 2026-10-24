"""Titulares por empresa desde Finnhub (`/company-news`; la clave gratuita permite 60 peticiones por minuto y un año de histórico).

Una petición por ticker, en serie y con el ritmo de `calls_per_minute`. Un 401/403 (clave mala) o un 429 que se repite paran la pasada con
`CompanyNotesError`; el fallo de un solo ticker no la tumba."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from scanner_opciones.marketdata.company import CompanyNotesError, NewsItem

log = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
RATE_LIMIT_WAIT = 30        # segundos de espera tras un 429 antes de reintentar una vez


def finnhub_symbol(ticker: str) -> str:
    """BRK-B / BRK B -> BRK.B (Finnhub escribe la clase de acciones con punto)."""
    return ticker.replace("-", ".").replace(" ", ".").upper()


def parse_news(rows) -> list[NewsItem]:
    out = []
    for r in rows or []:
        try:
            day = datetime.fromtimestamp(int(r["datetime"])).date()
            headline = str(r.get("headline") or "").strip()
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if headline:
            related = [s for s in str(r.get("related") or "").split(",") if s.strip()]
            out.append(NewsItem(day, headline, str(r.get("source") or ""), str(r.get("url") or ""), len(related)))
    return out


class FinnhubNews:
    """Implementa `NewsProvider`."""

    def __init__(self, token: str, calls_per_minute: float = 50) -> None:
        self.token = token
        self.pause = 60.0 / max(calls_per_minute, 1)
        self._client = None

    async def _get(self, path: str, params: dict):
        import httpx
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        for attempt in (1, 2):
            r = await self._client.get(f"{BASE_URL}{path}", params={**params, "token": self.token})
            if r.status_code == 429 and attempt == 1:
                log.info("Finnhub limita las peticiones: espero %d s", RATE_LIMIT_WAIT)
                await asyncio.sleep(RATE_LIMIT_WAIT)
                continue
            return r

    async def get_news(self, tickers: list[str], since: date, until: date) -> dict[str, list[NewsItem]]:
        out: dict[str, list[NewsItem]] = {}
        for k, ticker in enumerate(tickers):
            if k:
                await asyncio.sleep(self.pause)
            try:
                r = await self._get("/company-news", {"symbol": finnhub_symbol(ticker), "from": since.isoformat(), "to": until.isoformat()})
            except Exception as exc:        # red caída o similar: el ticker queda para otra vez
                log.debug("Finnhub: sin noticias de %s (%s)", ticker, exc)
                continue
            if r.status_code in (401, 403):
                raise CompanyNotesError(f"Finnhub rechaza la clave (HTTP {r.status_code}): revisa finnhub.api_token")
            if r.status_code == 429:
                raise CompanyNotesError("Finnhub sigue limitando las peticiones")
            if r.status_code != 200:
                log.debug("Finnhub: HTTP %s para %s", r.status_code, ticker)
                continue
            try:
                out[ticker] = parse_news(r.json())
            except ValueError:
                continue
        return out
