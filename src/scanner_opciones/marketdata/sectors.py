"""Sector de una empresa sin pasar por TWS (su consulta es lenta y exige conexión): primero el código SIC de SEC EDGAR, después Yahoo Finance.

Los nombres de sector son los de IBKR (`contractDetails.industry`: Technology, Financial, Energy, Utilities, Basic Materials, Industrial, Communications,
Consumer, Cyclical, Consumer, Non-cyclical), para que las exenciones de `scanner.quality.exempt_sectors` y los resúmenes por sector sigan funcionando igual
con cualquier fuente. Como en IBKR, las inmobiliarias y los REIT cuentan como «Financial» y la sanidad como «Consumer, Non-cyclical»."""
from __future__ import annotations

import logging
from typing import Optional, Protocol, Sequence

log = logging.getLogger(__name__)

TECH, FIN, ENERGY, UTIL, MAT, IND, COMM = "Technology", "Financial", "Energy", "Utilities", "Basic Materials", "Industrial", "Communications"
CYC, NONCYC = "Consumer, Cyclical", "Consumer, Non-cyclical"

# (SIC desde, SIC hasta, sector); gana el primer rango que contenga el código, así que los más estrechos van antes que su división
SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, NONCYC),
    (1000, 1099, MAT), (1200, 1399, ENERGY), (1400, 1499, MAT), (1500, 1799, IND),
    (2000, 2199, NONCYC), (2200, 2399, CYC), (2400, 2499, MAT), (2500, 2599, CYC), (2600, 2699, MAT), (2700, 2799, COMM),
    (2833, 2836, NONCYC), (2840, 2844, NONCYC), (2800, 2899, MAT), (2900, 2999, ENERGY),
    (3000, 3099, MAT), (3100, 3199, CYC), (3200, 3399, MAT), (3400, 3499, IND),
    (3559, 3559, TECH), (3570, 3579, TECH), (3500, 3569, IND), (3580, 3599, IND),
    (3661, 3669, COMM), (3670, 3679, TECH), (3600, 3699, IND),
    (3711, 3716, CYC), (3700, 3799, IND), (3820, 3829, TECH), (3840, 3859, NONCYC), (3800, 3899, IND), (3900, 3999, CYC),
    (4800, 4899, COMM), (4950, 4959, IND), (4900, 4999, UTIL), (4000, 4799, IND),
    (5045, 5045, TECH), (5122, 5122, NONCYC), (5000, 5199, IND), (5400, 5499, NONCYC), (5912, 5912, NONCYC), (5200, 5999, CYC),
    (6000, 6799, FIN),
    (7310, 7319, COMM), (7370, 7379, TECH), (7812, 7841, COMM), (7000, 7299, CYC), (7300, 7399, IND), (7500, 7599, CYC), (7800, 7999, CYC),
    (8000, 8299, NONCYC), (8731, 8731, NONCYC), (8700, 8799, IND),
)

# Sector de Yahoo Finance -> nombre de IBKR
YAHOO_SECTORS = {
    "technology": TECH, "financial services": FIN, "real estate": FIN, "energy": ENERGY, "utilities": UTIL, "basic materials": MAT, "industrials": IND,
    "consumer cyclical": CYC, "consumer defensive": NONCYC, "healthcare": NONCYC, "communication services": COMM,
}


def sic_to_sector(sic: object) -> Optional[str]:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    return next((sector for low, high, sector in SIC_RANGES if low <= code <= high), None)


def yahoo_to_sector(name: object) -> Optional[str]:
    return YAHOO_SECTORS.get(str(name or "").strip().lower())


class SectorProvider(Protocol):
    async def get_sectors(self, tickers: Sequence[str]) -> dict[str, tuple[str, Optional[str]]]:
        """{ticker: (sector, categoría)} solo de los que se conocen (los demás no aparecen)."""


class ChainedSectors:
    """Prueba los proveedores por orden y a cada uno solo le pide lo que los anteriores no resolvieron. Un proveedor que falla se salta."""

    def __init__(self, providers: Sequence[SectorProvider]) -> None:
        self.providers = list(providers)

    async def get_sectors(self, tickers: Sequence[str]) -> dict[str, tuple[str, Optional[str]]]:
        out: dict[str, tuple[str, Optional[str]]] = {}
        pending = list(dict.fromkeys(tickers))
        for provider in self.providers:
            if not pending:
                break
            try:
                found = await provider.get_sectors(pending)
            except Exception as exc:
                log.warning("Sector: %s no responde (%s); se prueba la siguiente fuente", type(provider).__name__, str(exc)[:120])
                continue
            out.update({t: v for t, v in found.items() if v and v[0]})
            pending = [t for t in pending if t not in out]
        return out

