"""Fuente de contratos del Market Radar de Tortuga: el spread que propone la ficha de cada valor (Bull Put Spread) y la put de 30Δ de referencia
de los de venta de put. No son operaciones de nadie sino propuestas del radar: no llevan trader y su fecha de apertura es la del dato del radar."""
from __future__ import annotations

from typing import Callable, Optional

from scanner_opciones.feeds.base import ContractIdea
from scanner_opciones.tortuga.radar import RadarData

MULTIPLIER = 100


class MarketRadarFeed:
    def __init__(self, data: Callable[[], Optional[RadarData]], name: str = "MarketRadar"):
        self.name = name
        self._data = data           # radar tal como lo dejaron las rutinas (prima de Tortuga, sin re-valorar ni restar comisiones)

    def contracts(self) -> list[ContractIdea]:
        data = self._data()
        if data is None:
            return []
        day = data.as_of or data.generated_at.date()
        out: list[ContractIdea] = []
        for r in data.rows:
            kind = r.kind
            if kind == "spread":     # solo con ficha: sin ella el radar no dice qué spread propone
                if r.has_spread and r.spread_expiry is not None and r.spread_premium is not None and r.spread_premium > 0:
                    width = r.spread_short - r.spread_long
                    out.append(ContractIdea(day, None, r.ticker, "Bull Put Spread", r.spread_expiry, r.spread_short, r.spread_long, 1,
                                            r.spread_premium, (width - r.spread_premium) * MULTIPLIER, self.name))
            elif kind == "put":
                if r.put_strike and r.put_expiry is not None and r.put_mid is not None and r.put_mid > 0:
                    out.append(ContractIdea(day, None, r.ticker, "Short Put", r.put_expiry, r.put_strike, None, 1, r.put_mid,
                                            (r.put_strike - r.put_mid) * MULTIPLIER, self.name))
        return out

    def tickers(self) -> list[str]:
        return sorted({c.ticker for c in self.contracts()})

    def stamp(self) -> Optional[str]:
        data = self._data()
        return data.generated_at.isoformat() if data is not None else None
