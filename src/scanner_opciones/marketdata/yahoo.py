"""Cambios de valoración de analistas desde Yahoo Finance (librería `yfinance`: gratuita, sin clave y no oficial, así que puede
romperse o limitar las peticiones; en ese caso se conserva lo guardado). Único módulo que importa `yfinance`."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Callable, Optional

from scanner_opciones.marketdata.analysts import AnalystChange, AnalystError, classify_changes
from scanner_opciones.marketdata.sectors import yahoo_to_sector

log = logging.getLogger(__name__)

MAX_FAILURES = 8        # fallos seguidos que cortan la pasada (Yahoo limita las peticiones)


def yahoo_symbol(ticker: str) -> str:
    """BRK.B / BRK B -> BRK-B (Yahoo escribe la clase de acciones con guion)."""
    return ticker.replace(".", "-").replace(" ", "-").upper()


def rows_from_frame(frame) -> list[dict]:
    """Filas normalizadas del DataFrame `upgrades_downgrades` de yfinance (índice = fecha del cambio)."""
    if frame is None or len(frame) == 0:
        return []
    out = []
    for stamp, r in frame.iterrows():
        day = stamp.date() if hasattr(stamp, "date") else stamp
        out.append({"day": day, "firm": r.get("Firm"), "action": r.get("Action"), "from": r.get("FromGrade"), "to": r.get("ToGrade"),
                    "pt_from": r.get("priorPriceTarget"), "pt_to": r.get("currentPriceTarget")})
    return out


class YahooAnalysts:
    """Implementa `AnalystProvider`. Una petición por ticker (~0,15 s), en hilos porque `yfinance` es síncrono."""

    def __init__(self, concurrency: int = 4, today: Callable[[], date] = lambda: datetime.now().date()) -> None:
        self.concurrency = concurrency
        self.today = today

    async def get_analyst_changes(self, tickers: list[str], window_days: int) -> dict[str, list[AnalystChange]]:
        try:
            import yfinance
        except ImportError as exc:
            raise AnalystError("Falta la librería yfinance (pip install yfinance)") from exc
        # yfinance escribe en su log como ERROR cada «404: sin datos» (ETF, índices, valores retirados: no tienen analistas) y no es un fallo
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, list[AnalystChange]] = {}
        failures = 0

        def fetch(symbol: str):
            return yfinance.Ticker(symbol).upgrades_downgrades

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    frame = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:        # Yahoo cambia a menudo: el fallo de un ticker no tumba la pasada
                    failures += 1
                    log.debug("Yahoo: sin cambios de analistas de %s (%s)", ticker, exc)
                    return
            failures = 0
            out[ticker] = classify_changes(rows_from_frame(frame), self.today(), window_days)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise AnalystError("Yahoo Finance no responde o limita las peticiones")
        return out


class YahooSectors:
    """Implementa `SectorProvider` con `Ticker.info` de Yahoo (sector e industria), traducido a los nombres de IBKR. Una petición por ticker (~0,5 s)."""

    def __init__(self, concurrency: int = 4) -> None:
        self.concurrency = concurrency

    async def get_sectors(self, tickers) -> dict[str, tuple[str, Optional[str]]]:
        try:
            import yfinance
        except ImportError as exc:
            raise AnalystError("Falta la librería yfinance (pip install yfinance)") from exc
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        sem = asyncio.Semaphore(self.concurrency)
        out: dict[str, tuple[str, Optional[str]]] = {}
        failures = 0

        def fetch(symbol: str) -> dict:
            return yfinance.Ticker(symbol).info or {}

        async def one(ticker: str) -> None:
            nonlocal failures
            if failures >= MAX_FAILURES:
                return
            async with sem:
                try:
                    info = await asyncio.to_thread(fetch, yahoo_symbol(ticker))
                except Exception as exc:
                    failures += 1
                    log.debug("Yahoo: sin sector de %s (%s)", ticker, exc)
                    return
            failures = 0
            sector = yahoo_to_sector(info.get("sector"))
            if sector:
                out[ticker] = (sector, info.get("industry") or None)

        await asyncio.gather(*(one(t) for t in tickers))
        if failures >= MAX_FAILURES:
            raise AnalystError("Yahoo Finance no responde o limita las peticiones")
        return out
