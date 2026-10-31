"""Vuelca el Feed de la comunidad (operaciones compartidas) en el libro `community_trades`, para acumular el historial apertura->cierre de cada
trader aunque la tarjeta salga de la ventana del Feed. Lee lo que ya dejó la rutina diaria (no pide nada a Tortuga) y solo guarda si el JSON es
nuevo (su `generated_at` cambió respecto al último volcado). No interpreta ni valora: delega el cálculo de las métricas de salida en `FeedTrade`."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from scanner_opciones.community.feed_data import FeedData
from scanner_opciones.storage.community_trades import CommunityTradeRepo

log = logging.getLogger(__name__)


class CommunityTradeLogJob:
    def __init__(self, repo: CommunityTradeRepo, feed: Callable[[], Optional[FeedData]],
                 last_stamp: Callable[[], Optional[str]], save_stamp: Callable[[str], None],
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.repo = repo
        self.feed = feed                    # los datos del Feed ya parseados (None si no hay fichero o es inválido)
        self.last_stamp = last_stamp        # `generated_at` del último Feed volcado (de `meta`)
        self.save_stamp = save_stamp
        self.now = now

    def run(self, force: bool = False) -> dict[str, int]:
        data = self.feed()
        if data is None or not data.trades:
            return {"new": 0, "closed": 0, "updated": 0}
        stamp = data.generated_at.isoformat()
        if not force and self.last_stamp() == stamp:
            return {"new": 0, "closed": 0, "updated": 0}
        summary = self.repo.upsert_many(data.trades, self.now().isoformat(timespec="seconds"))
        self.save_stamp(stamp)
        if summary["new"] or summary["closed"]:
            log.info("Feed de la comunidad: %d operaciones nuevas, %d cerradas", summary["new"], summary["closed"])
        return summary
