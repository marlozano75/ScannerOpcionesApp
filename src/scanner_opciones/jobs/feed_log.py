"""Registro en el Histórico de los contratos de las fuentes de contratos (hoy la comunidad de Tortuga), con su trader.

Un contrato de una fuente se anota en el mismo registro que los escaneos (`storage/scan_log.py`), con `origin` = nombre de la fuente, el trader que
lo abrió y la fecha de apertura del propio contrato (`opened`). Su «perfil» es el perfil de escaneo al que ENCAJA hoy (`community/fit.py`: cumple todo
lo medible); un contrato que cumple varios se anota una vez por cada uno, y uno que no encaja en ninguno, como «Sin perfil». Aparece y desaparece
(vencido, ya no está en la fuente, ya no encaja) igual que en un escaneo. Lee lo ya valorado: no pide nada al bróker."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from scanner_opciones.community.ideas import EXPIRED, IdeaView
from scanner_opciones.scanner.profiles import UNASSIGNED
from scanner_opciones.storage.scan_log import Hit, LoggedRow, ScanLogRepo

log = logging.getLogger(__name__)
MULTIPLIER = 100      # las ideas de la comunidad son opciones de acciones de EE. UU.


def idea_hit(v: IdeaView) -> Hit:
    """El contrato de una fuente, valorado con los datos de hoy, como una fila del registro."""
    i = v.idea
    premium = v.premium_now
    annual = v.yield_now_pct * 365 / v.dte if v.yield_now_pct is not None and v.dte > 0 else None
    return Hit(i.ticker, i.expiry.isoformat(), i.short_strike, i.long_strike, premium, annual, v.dte, v.otm_now_pct,
               credit=premium * MULTIPLIER if premium is not None else None,
               risk=(v.risk_base - premium) * MULTIPLIER if premium is not None else None,
               delta=v.delta_now, iv_rank=v.iv_rank, iv_pct=v.iv_percentile, trader=i.trader, opened=i.day.isoformat())


def _measurable(v: IdeaView) -> bool:
    """¿Se pudo medir la distancia del strike con algún perfil? Si no (sin precio ni cotización), no se sabe a qué perfil encaja y el contrato se deja como estaba."""
    return any(f is not None and f.core_known for f in v.fits)


class FeedLogJob:
    def __init__(self, repo: ScanLogRepo, views: Callable[[], list[IdeaView]], profile_names: Callable[[], list[str]],
                 has_data: Callable[[str], bool] = lambda source: True, now: Callable[[], datetime] = datetime.now) -> None:
        self.repo = repo
        self.has_data = has_data            # ¿tiene datos la fuente? Sin ellos (fichero ausente) no se da por desaparecido nada
        self.views = views                  # los contratos de todas las fuentes, valorados (cada `IdeaView.fits` en el orden de `profile_names`)
        self.profile_names = profile_names
        self.now = now

    def run(self) -> dict[str, int]:
        summary = {"profiles": 0, "appeared": 0, "vanished": 0}
        views = self.views()
        names = self.profile_names()
        for source in sorted({v.idea.source for v in views} | {o for o in self.repo.origins() if o != "scan"}):
            if not self.has_data(source):
                continue
            try:
                appeared, vanished, groups = self._log_source(source, [v for v in views if v.idea.source == source], names)
            except Exception:  # noqa: BLE001 - una fuente rota no debe impedir las demás ni el escaneo
                log.exception("Histórico: falló el registro de los contratos de «%s»", source)
                continue
            summary["profiles"] += groups
            summary["appeared"] += appeared
            summary["vanished"] += vanished
        if summary["appeared"] or summary["vanished"]:
            log.info("Histórico de fuentes: %d contratos nuevos, %d desaparecen", summary["appeared"], summary["vanished"])
        return summary

    def _log_source(self, source: str, views: list[IdeaView], names: list[str]) -> tuple[int, int, int]:
        groups: dict[tuple[str, str], dict[tuple, Hit]] = {}      # (perfil, tipo) -> contratos que encajan
        info: dict[tuple, IdeaView] = {}
        expired: set[tuple] = set()
        skipped: set[tuple] = set()
        for v in views:
            hit = idea_hit(v)
            info[hit.key] = v
            if v.status == EXPIRED:
                expired.add(hit.key)
            elif not _measurable(v):
                skipped.add(hit.key)
            else:
                kind = "spread" if v.idea.long_strike is not None else "put"
                fitting = [names[i] for i, f in enumerate(v.fits) if f is not None and f.fits and i < len(names)] or [UNASSIGNED]
                for profile in fitting:
                    groups.setdefault((profile, kind), {})[hit.key] = hit
        now = self.now()
        at = now.isoformat(timespec="seconds")
        appeared_n = vanished_n = runs = 0
        for profile, kind in sorted(set(groups) | set(self.repo.active_groups(source))):
            current = groups.get((profile, kind), {})
            old = {r.key: r for r in self.repo.active(profile, kind, source)}
            appeared = [h for k, h in current.items() if k not in old]
            kept = [(old[k], h) for k, h in current.items() if k in old]
            vanished = [(r, self._reason(r, profile, info, expired, names)) for k, r in old.items() if k not in current and k not in skipped]
            if not (appeared or kept or vanished):
                continue
            self.repo.apply(at, profile, kind, appeared, kept, vanished, len(skipped), origin=source)
            appeared_n, vanished_n, runs = appeared_n + len(appeared), vanished_n + len(vanished), runs + 1
        return appeared_n, vanished_n, runs

    @staticmethod
    def _reason(row: LoggedRow, profile: str, info: dict[tuple, IdeaView], expired: set[tuple], names: list[str]) -> str:
        if row.key in expired:
            return "vencido"
        v = info.get(row.key)
        if v is None:
            return "ya no está en la fuente"
        if profile == UNASSIGNED:
            fitting = [names[i] for i, f in enumerate(v.fits) if f is not None and f.fits and i < len(names)]
            return "ahora encaja en " + ", ".join(fitting) if fitting else "sin cambios"
        fit: Optional[object] = next((f for i, f in enumerate(v.fits) if i < len(names) and names[i] == profile), None)
        failed = getattr(fit, "failed", ())
        return "ya no encaja: " + "; ".join(failed) if failed else "ya no encaja"
