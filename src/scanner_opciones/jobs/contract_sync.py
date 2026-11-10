"""Catálogo incremental de contratos guardados por ticker, compartido por la actualización diaria y el refresco."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Callable, Optional

from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionChain, OptionContract
from scanner_opciones.scanner.candidates import call_candidate_contracts, candidate_contracts
from scanner_opciones.storage.repositories import ContractRepo

log = logging.getLogger(__name__)


class ContractSyncer:
    """Solo se validan con el broker/proveedor las combinaciones que ni están guardadas ni se sabe que no existen.
    Los contratos que siguen en la ventana conservan su snapshot. La ventana es la guardada
    (`scanner.catalog`: el rango visible más su margen) calculada con el precio actual del subyacente.
    La cadena se cachea en memoria por día: strikes y vencimientos no cambian dentro de la sesión."""

    def __init__(self, gateway: BrokerGateway, contracts: ContractRepo, settings: Settings) -> None:
        self.gateway = gateway
        self.contracts = contracts
        self.settings = settings
        self._chains: dict[str, tuple[date, OptionChain]] = {}
        # {ticker: TickerModes} de la última evaluación (lo pone el servicio); vacío o None = sin límite (ver `scanner.ticker_modes.gate_catalog`)
        self.modes_provider: Optional[Callable[[], dict]] = None
        # contratos (puts y calls, vendidos o comprados) que hay en cartera, de tickers de la watchlist (lo pone el servicio): se guardan SIEMPRE,
        # estén o no en la ventana del catálogo (ITM, DTE largo, strike cercano…), para que se coticen y tengan su margen what-if
        self.held_provider: Optional[Callable[[], list[OptionContract]]] = None

    def held(self, ticker: str) -> list[OptionContract]:
        if self.held_provider is None:
            return []
        return [c for c in self.held_provider() if c.ticker == ticker]

    def add_held(self, ticker: str) -> int:
        """Guarda los contratos en cartera que faltan en el catálogo (sin pedir nada al broker: la posición ya trae su `con_id`)."""
        held = self.held(ticker)
        if not held:
            return 0
        have = self.contracts.keys(ticker)
        missing = [c for c in held if ContractRepo.key(c) not in have]
        if not missing:
            return 0
        _, inserted = self.contracts.sync_for_ticker(ticker, have | {ContractRepo.key(c) for c in held}, missing)
        return inserted

    def _gated(self, ticker: str) -> Optional[dict]:
        """{'puts': bool, 'calls': bool} con lo que los modos del ticker permiten estrenar; None si no hay límite."""
        if self.modes_provider is None or not self.settings.scanner.ticker_modes.gate_catalog:
            return None
        mode = (self.modes_provider() or {}).get(ticker)
        if mode is None or mode.all_on:
            return None
        return {"puts": mode.any_puts, "calls": mode.cds}

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
        if self.settings.scanner.cds.enabled:       # calls de los Call Debit Spread (DTE largo, por encima del precio)
            wanted += call_candidate_contracts(chain, price, today, self.settings.scanner.cds)
        held = self.held(ticker)
        wanted_keys = {ContractRepo.key(c) for c in wanted} | {ContractRepo.key(c) for c in held}
        if revalidate:
            self.contracts.clear_misses(ticker)
        have = self.contracts.keys(ticker)
        missed = self.contracts.miss_keys(ticker)
        to_check = [c for c in wanted if (k := ContractRepo.key(c)) not in have and k not in missed]
        skipped = 0
        if self._gated(ticker):
            allowed = self._gated(ticker)
            kept = [c for c in to_check if (c.right is OptionRight.CALL and allowed["calls"]) or (c.right is OptionRight.PUT and allowed["puts"])]
            skipped, to_check = len(to_check) - len(kept), kept
        validated: list = []
        if to_check:  # descarta strikes que no existen para ese vencimiento y guarda el conId
            t0 = time.monotonic()
            validated = await self.gateway.qualify_contracts(to_check)
            if timings is not None:
                timings["validación"] += time.monotonic() - t0
            ok = {ContractRepo.key(c) for c in validated}
            self.contracts.add_misses(ticker, [c for c in to_check if ContractRepo.key(c) not in ok])
            log.info(
                "%s: %d de %d combinaciones nuevas existen (las demás no están listadas; es normal)",
                ticker, len(validated), len(to_check),
            )
        if skipped:
            log.debug("%s: %d contratos nuevos sin cualificar (el ticker no tiene el modo que los usa)", ticker, skipped)
        validated = list(validated) + [c for c in held if ContractRepo.key(c) not in have]     # en cartera: ya tienen con_id
        removed, inserted = self.contracts.sync_for_ticker(ticker, wanted_keys, validated)
        self.contracts.purge_expired_misses(today)
        if removed:
            log.info("%s: %d contratos retirados (vencidos o fuera de la ventana guardada)", ticker, removed)
        return removed, inserted
