"""Perfil «MTO CDS» (Call Debit Spread muy OTM; ver docs/mto-spreads.md) dentro del Scanner (`/scanner?mode=cds`).

El CDS es un tercer instrumento de los perfiles del Scanner: comparte la plantilla `scanner.html` (barra de perfiles, filtros plegables a la
izquierda, resultados con «Base / Activados / Desactivados», panel «¿Cuánto descarta cada filtro?» y tabla con miniaturas) y añade sus propias
secciones de filtros y columnas. Aquí está lo que no es de plantilla: lectura del formulario, escaneo y panel de impacto. La dirección antigua
`/cds` redirige al Scanner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.cds import CdsResult
from scanner_opciones.scanner.profiles import Profile, profile_diff
from scanner_opciones.ui import cds_form
from scanner_opciones.ui.scanner_forms import profile_href

MAX_ROWS = 300


@dataclass(frozen=True)
class CdsRow:
    """Una fila de la tabla: el spread, la ficha del subyacente y su sector."""
    res: CdsResult
    info: Optional[TickerInfo]
    dte: int

    @property
    def snapshot(self):
        """La call comprada: las macros de columnas compartidas (`_contract_cols.html`) leen de aquí el contrato y la hora de la cotización."""
        return self.res.long

    @property
    def sector(self) -> str:
        return (self.info.sector if self.info and self.info.sector else "") or ""


def active_profile(profiles: list[Profile], qp) -> Optional[Profile]:
    """Perfil CDS pedido en la URL (`profile`) o, si la consulta no trae filtros (`submitted`), el primero."""
    cds_profiles = [p for p in profiles if p.mode == "cds"]
    active = next((p for p in cds_profiles if p.name == qp.get("profile", "")), None)
    if "submitted" not in qp:
        active = active or (cds_profiles[0] if cds_profiles else None)
    return active


def read(service, profiles: list[Profile], qp) -> tuple[Optional[Profile], dict]:
    """(perfil activo, formulario leído). Sin `submitted` se cargan los filtros del perfil (o los de config.yaml si no hay ninguno)."""
    active = active_profile(profiles, qp)
    if "submitted" not in qp:
        params = {**(active.set_params("cds") if active else {}), "submitted": "1", "profile": active.name if active else ""}
    else:
        params = dict(qp)
    return active, cds_form.parse_cds(service, params)


def page(service, profiles: list[Profile], qp) -> dict:
    """Contexto específico del CDS para `scanner.html` (el resto —perfiles, avisos…— lo añade quien llama)."""
    catalog = service.settings.scanner.cds
    active, parsed = read(service, profiles, qp)
    rows: list[CdsResult] = []
    if catalog.enabled and parsed["cds"] is not None:
        rows = service.scan_cds(parsed["cds"], **parsed["ref"])
    infos, today = service.infos(), service.now().date()
    form = {**parsed["form"], "mode": "cds", "set": "cds"}
    saved = {k: v for k, v in active.set_params("cds").items()} if active else {}
    diff = profile_diff(saved, cds_form.form_query(parsed["form"])) if active else {}
    return dict(is_cds=True, cds_cfg=catalog, cds_rows=[CdsRow(r, infos.get(r.long.contract.ticker), (r.long.contract.expiry - today).days) for r in rows[:MAX_ROWS]], cds_total=len(rows),
                cds_max_rows=MAX_ROWS, form=form, error=parsed["error"], warnings=list(parsed["notices"]), active=active, diff=diff,
                widths_all=catalog.width_options, cds_ok=parsed["cds"] is not None)


def impact(service, profiles: list[Profile], qp):
    """(informe, error) del panel «¿Cuánto descarta cada filtro?» del CDS con los filtros de la consulta."""
    _, parsed = read(service, profiles, qp)
    if parsed["cds"] is None:
        return None, parsed["error"] or "Parámetros no válidos"
    return service.scan_cds_impact(parsed["cds"], **parsed["ref"]), None


def register(app, service, get_profiles) -> None:
    @app.get("/cds")
    async def cds(request: Request):
        """Dirección antigua de la página del CDS: ahora es el Scanner con el instrumento `cds` (se conservan los filtros de la consulta)."""
        qp = dict(request.query_params)
        if qp:
            return RedirectResponse("/scanner?" + urlencode({**qp, "mode": "cds"}), status_code=303)
        profile = next((p for p in get_profiles()[0] if p.mode == "cds"), None)
        return RedirectResponse(profile_href(profile) if profile else "/scanner?" + urlencode({"mode": "cds"}), status_code=303)
