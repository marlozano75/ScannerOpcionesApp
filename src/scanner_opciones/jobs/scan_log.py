"""Escaneo automático de perfiles con registro de apariciones y desapariciones (y su motivo). Lee snapshots guardados: no pide nada al bróker."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Callable, Optional

from scanner_opciones.config.settings import ScanLogSettings
from scanner_opciones.jobs.scan_log_sheets import SheetsWriter
from scanner_opciones.scanner.engine import ScanOutput
from scanner_opciones.storage.scan_log import Hit, LoggedRow, ScanLogRepo

log = logging.getLogger(__name__)


def put_hits(out: ScanOutput) -> list[Hit]:
    hits = []
    for r in out.results:
        s = r.snapshot
        c = s.contract
        net = r.net_price if r.net_price is not None else r.reference_price
        hits.append(Hit(c.ticker, c.expiry.isoformat(), c.strike, None, net, r.yield_ref_annualized_pct, r.dte, r.strike_distance_pct,
                        credit=net * c.multiplier if net is not None else None, risk=(c.strike - net) * c.multiplier if net is not None else None,
                        delta=s.delta, yield_bid=r.yield_bid_annualized_pct, iv_rank=s.iv_rank, iv_pct=s.iv_percentile, spread_pct=s.spread_pct,
                        bid_size=s.bid_size))
    return hits


def spread_hits(out: ScanOutput) -> list[Hit]:
    hits = []
    for sp in out.spreads:
        s = sp.short.snapshot
        c = s.contract
        hits.append(Hit(c.ticker, c.expiry.isoformat(), c.strike, sp.long.contract.strike, sp.credit, sp.return_on_risk_annualized_pct,
                        sp.short.dte, sp.discount_pct if sp.discount_pct is not None else sp.short.strike_distance_pct,
                        credit=sp.credit * c.multiplier, risk=sp.max_loss, delta=s.delta, long_delta=sp.long.delta, iv_rank=s.iv_rank,
                        iv_pct=s.iv_percentile, spread_pct=s.spread_pct, bid_size=s.bid_size))
    return hits


def vanish_reason(row: LoggedRow, rejections: dict[str, str], today: date) -> str:
    """Por qué un contrato ya no sale: el filtro que lo descarta ahora o, si no lo evalúa ninguno, la causa."""
    why = rejections.get(row.rejection_key)
    if why:
        return why
    if row.expiry < today.isoformat():
        return "vencido"
    if row.kind == "spread":
        return "el spread ya no se forma (pata comprada, crédito o ancho)"
    return "sin cotización o fuera de los contratos guardados"


class ScanLogJob:
    def __init__(self, repo: ScanLogRepo, cfg: ScanLogSettings, scan: Callable[..., ScanOutput], profile_criteria: Callable[[], list[tuple]],
                 is_open: Callable[[str], bool], now: Callable[[], datetime] = datetime.now, writer: Optional[SheetsWriter] = None) -> None:
        self.repo = repo
        self.cfg = cfg
        self.scan = scan                          # (criterios, include_rejections) -> ScanOutput
        self.profile_criteria = profile_criteria  # [(perfil, criterios de put | None, criterios de spread | None)]
        self.is_open = is_open                    # ¿está abierto el mercado de este ticker?
        self.now = now
        self.writer = writer
        self.last_run: Optional[datetime] = None
        self.last_summary: dict[str, int] = {}

    async def run(self) -> dict[str, int]:
        """Una pasada de todos los perfiles elegidos; después sube lo nuevo al Google Sheet (si está activado)."""
        summary = self.scan_all()
        if self.writer is not None:
            await asyncio.to_thread(self.writer.sync)
        return summary

    def scan_all(self) -> dict[str, int]:
        summary = {"profiles": 0, "appeared": 0, "vanished": 0}
        for name, put_crit, spread_crit in self.profile_criteria():
            if self.cfg.profiles and name not in self.cfg.profiles:
                continue
            for kind, crit in (("put", put_crit), ("spread", spread_crit)):
                if crit is None:
                    continue
                try:
                    appeared, vanished = self._scan_one(name, kind, crit)
                except Exception:  # noqa: BLE001 - un perfil roto no debe impedir los demás
                    log.exception("Escaneo automático: falló el perfil «%s» (%s)", name, kind)
                    continue
                summary["profiles"] += 1
                summary["appeared"] += appeared
                summary["vanished"] += vanished
        self.last_run, self.last_summary = self.now(), summary
        if summary["appeared"] or summary["vanished"]:
            log.info("Escaneo automático: %d perfiles, %d contratos nuevos, %d desaparecen", summary["profiles"], summary["appeared"], summary["vanished"])
        return summary

    def _scan_one(self, profile: str, kind: str, criteria) -> tuple[int, int]:
        out = self.scan(criteria.with_filters(spread_mode=(kind == "spread")), True)
        hits = spread_hits(out) if kind == "spread" else put_hits(out)
        open_cache: dict[str, bool] = {}

        def open_(ticker: str) -> bool:
            if ticker not in open_cache:
                open_cache[ticker] = self.is_open(ticker)
            return open_cache[ticker]

        # los tickers con el mercado cerrado se dejan como estaban: no «aparecen» ni «desaparecen» por no tener cotizaciones nuevas
        now = self.now()
        previous = self.repo.active(profile, kind)
        current = {h.key: h for h in hits if open_(h.ticker)}
        old = {r.key: r for r in previous if open_(r.ticker)}
        skipped = len({h.ticker for h in hits if not open_(h.ticker)} | {r.ticker for r in previous if not open_(r.ticker)})
        if not current and not old and skipped:
            return 0, 0      # todo cerrado: nada que anotar
        at = now.isoformat(timespec="seconds")
        today = now.date()
        appeared = [h for k, h in current.items() if k not in old]
        kept = [(old[k], h) for k, h in current.items() if k in old]
        vanished = [(r, vanish_reason(r, out.rejections, today)) for k, r in old.items() if k not in current]
        self.repo.apply(at, profile, kind, appeared, kept, vanished, skipped)
        return len(appeared), len(vanished)
