"""Catálogo incremental de contratos guardados por ticker, compartido por la actualización diaria y el refresco."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import OptionChain
from scanner_opciones.scanner.candidates import candidate_contracts
from scanner_opciones.storage.repositories import ContractRepo

log = logging.getLogger(__name__)


class ContractSyncer:
    """Solo se validan con IBKR las combinaciones que ni están guardadas ni se sabe que no existen.
    Los contratos que siguen en la ventana conservan su snapshot. La ventana es la guardada
    (`scanner.catalog`: el rango visible más su margen) calculada con el precio actual del subyacente.
    La cadena se cachea en memoria por día: strikes y vencimientos no cambian dentro de la sesión."""

    def __init__(self, gateway: BrokerGateway, contracts: ContractRepo, settings: Settings) -> None:
        self.gateway = gateway
        self.contracts = contracts
        self.settings = settings
        self._chains: dict[str, tuple[date, OptionChain]] = {}

    def remember_chain(self, ticker: str, chain: OptionChain, today: date) -> None:
        self._chains[ticker] = (today, chain)

    async def chain(self, ticker: str, today: date) -> OptionChain:
        cached = self._chains.get(ticker)
        if cached is not None and cached[0] == today:
            return cached[1]
        chain = await self.gateway.get_option_chain(ticker)
        self.remember_chain(ticker, chain, today)
        return chain

    async def sync(
        self, ticker: str, chain: OptionChain, price: float, today: date,
        revalidate: bool = False, timings: Optional[dict[str, float]] = None,
    ) -> tuple[int, int]:
        """Devuelve (contratos retirados, contratos nuevos guardados)."""
        wanted = candidate_contracts(chain, price, today, self.settings.scanner.catalog)
        wanted_keys = {ContractRepo.key(c) for c in wanted}
        if revalidate:
            self.contracts.clear_misses(ticker)
        have = self.contracts.keys(ticker)
        missed = self.contracts.miss_keys(ticker)
        to_check = [c for c in wanted if (k := ContractRepo.key(c)) not in have and k not in missed]
        validated: list = []
        if to_check:  # descarta strikes que no existen para ese vencimiento y guarda el conId
            t0 = time.monotonic()
            validated = await self.gateway.qualify_contracts(to_check)
            if timings is not None:
                timings["validación"] += time.monotonic() - t0
            ok = {ContractRepo.key(c) for c in validated}
            self.contracts.add_misses(ticker, [c for c in to_check if ContractRepo.key(c) not in ok])
            log.info(
                "%s: %d de %d combinaciones nuevas existen en IBKR (las demás no están listadas; es normal)",
                ticker, len(validated), len(to_check),
            )
        removed, inserted = self.contracts.sync_for_ticker(ticker, wanted_keys, validated)
        self.contracts.purge_expired_misses(today)
        if removed:
            log.info("%s: %d contratos retirados (vencidos o fuera de la ventana guardada)", ticker, removed)
        return removed, inserted
