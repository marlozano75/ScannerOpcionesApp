"""Perfil «MTO CDS» (Call Debit Spread muy OTM; ver docs/mto-spreads.md) dentro del Scanner (`/scanner?mode=cds`).

El CDS es un tercer instrumento de los perfiles del Scanner: comparte la plantilla `scanner.html` (barra de perfiles, filtros plegables a la
izquierda, resultados con «Base / Activados / Desactivados», panel «¿Cuánto descarta cada filtro?» y tabla con miniaturas) y añade sus propias
secciones de filtros y columnas. Aquí está lo que no es de plantilla: lectura del formulario, escaneo y panel de impacto. La dirección antigua
`/cds` redirige al Scanner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.cds import CdsResult
from scanner_opciones.scanner.criteria import unavailable_ma_fields
from scanner_opciones.scanner.profiles import Profile, profile_diff
from scanner_opciones.ui import cds_form
from scanner_opciones.ui.common import NO_DIV_NOTICE
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
        params = {**(cds_form.with_allw(active.set_params("cds")) if active else {}), "submitted": "1", "profile": active.name if active else ""}
    else:
        params = dict(qp)
    return active, cds_form.parse_cds(service, params)


def page(service, profiles: list[Profile], qp) -> dict:
    """Contexto específico del CDS para `scanner.html` (el resto —perfiles, avisos…— lo añade quien llama)."""
    catalog, qcfg = service.settings.scanner.cds, service.settings.scanner.quality
    active, parsed = read(service, profiles, qp)
    rows: list[CdsResult] = []
    if catalog.enabled and parsed["cds"] is not None:
        rows = service.scan_cds(parsed["cds"], **parsed["ref"])
    infos, today = service.infos(), service.now().date()
    notices = list(parsed["notices"])
    if "use_div" in qp:
        marks = service.div_marks()
        rows = [r for r in rows if r.long.contract.ticker.replace(" ", "-") in marks]
        if not marks:
            notices.append(NO_DIV_NOTICE)
    form = {**parsed["form"], "mode": "cds", "set": "cds"}
    saved = dict(cds_form.with_allw(active.set_params("cds"))) if active else {}
    diff = profile_diff(saved, cds_form.form_query(parsed["form"])) if active else {}
    return dict(is_cds=True, cds_cfg=catalog, cds_rows=[CdsRow(r, infos.get(r.long.contract.ticker), (r.long.contract.expiry - today).days) for r in rows[:MAX_ROWS]], cds_total=len(rows),
                cds_max_rows=MAX_ROWS, form=form, error=parsed["error"], warnings=notices, active=active, diff=diff,
                widths_all=catalog.width_options, cds_ok=parsed["cds"] is not None, source_names=[src.name for src in service.universe_sources],
                quality_opts=dict(liquidity=qcfg.liquidity_options, quarters=qcfg.positive_quarters_options, leverage=qcfg.leverage_options),
                ma_hidden=unavailable_ma_fields(parsed["form"]["ma_frame"], service.settings.trend.history_days, service.settings.scanner.technical.ma_slope_candles),
                tech_opts=dict(slope_candles=service.settings.scanner.technical.ma_slope_candles, rsi_lookback=service.settings.scanner.technical.rsi_lookback_days),
                count_text=lambda *_: "")


def impact(service, profiles: list[Profile], qp):
    """(informe, error) del panel «¿Cuánto descarta cada filtro?» del CDS con los filtros de la consulta."""
    _, parsed = read(service, profiles, qp)
    if parsed["cds"] is None:
        return None, parsed["error"] or "Parámetros no válidos"
    return service.scan_cds_impact(parsed["cds"], **parsed["ref"]), None


def _num(text: str) -> float:
    return float(text.strip().replace(",", "."))


def check(service, profiles: list[Profile], qp):
    """(consulta, error) de un CDS concreto con los filtros de la consulta: `ck_ticker`, `ck_expiry` (AAAA-MM-DD), `ck_long` y `ck_short` (strikes)."""
    _, parsed = read(service, profiles, qp)
    if parsed["cds"] is None:
        return None, parsed["error"] or "Parámetros no válidos"
    try:
        ticker = (qp.get("ck_ticker") or "").strip()
        if not ticker:
            return None, "Escribe el ticker."
        expiry = date.fromisoformat((qp.get("ck_expiry") or "").strip())
        long_k, short_k = _num(qp.get("ck_long") or ""), _num(qp.get("ck_short") or "")
    except ValueError:
        return None, "Revisa el vencimiento (fecha completa) y los dos strikes (números)."
    return service.check_cds(ticker, expiry, long_k, short_k, parsed["cds"], **parsed["ref"]), None


def register(app, service, get_profiles) -> None:
    @app.get("/cds")
    async def cds(request: Request):
        """Dirección antigua de la página del CDS: ahora es el Scanner con el instrumento `cds` (se conservan los filtros de la consulta)."""
        qp = dict(request.query_params)
        if qp:
            return RedirectResponse("/scanner?" + urlencode({**qp, "mode": "cds"}), status_code=303)
        profile = next((p for p in get_profiles()[0] if p.mode == "cds"), None)
        return RedirectResponse(profile_href(profile) if profile else "/scanner?" + urlencode({"mode": "cds"}), status_code=303)
