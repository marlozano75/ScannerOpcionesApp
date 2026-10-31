"""Pestaña Histórico: configuración del escaneo automático de perfiles y del volcado a Google Sheets, y su contenido tal como se vuelca."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scanner_opciones.jobs.scan_log_sheets import (ACTIVE_HEADER, ACTIVE_TAB, EVENTS_HEADER, EVENTS_TAB, RUNS_HEADER, RUNS_TAB, active_row,
                                                   event_row, run_row)
from scanner_opciones.ui.common import MAX_FORM_FIELDS, _grp, message_groups

TABS = ((EVENTS_TAB, "Eventos"), (RUNS_TAB, "Escaneos"), (ACTIVE_TAB, "Activos"))
LIMIT = 500      # filas que muestra la página (el Google Sheet recibe todas)


def register(app, service, render, remembered) -> None:
    @app.get("/historico", response_class=HTMLResponse)
    async def historico(request: Request, tab: str = EVENTS_TAB, message: str = "", origen: str = "", trader: str = ""):
        tab = tab if tab in {t for t, _ in TABS} else EVENTS_TAB
        repo, cfg = service.scan_log_repo, service.scan_log_cfg()
        if tab == EVENTS_TAB:
            header, rows = EVENTS_HEADER, [event_row(e) for e in repo.recent_events(LIMIT, origen, trader)]
        elif tab == RUNS_TAB:
            header, rows = RUNS_HEADER, [run_row(r) for r in repo.recent_runs(LIMIT, origen)]       # las pasadas no llevan trader
        else:
            header, rows = ACTIVE_HEADER, [active_row(r) for r in repo.all_active(origen, trader)[:LIMIT]]
        job = service.scan_log
        writer = job.writer
        return render(request, "historico.html", no_autorefresh=True, watch_data=True, cfg=cfg, tab=tab, tabs=TABS, header=header, rows=rows,
                      limit=LIMIT, counts=repo.counts(), profiles=[p[0] for p in service.profile_specs()], last_run=job.last_run,
                      last_summary=job.last_summary, origen=origen, trader=trader, origins=repo.origins(), traders=repo.traders(), sheets_error=writer.last_error if writer is not None else None, message=message,
                      groups=message_groups(message))

    @app.post("/historico/config")
    async def historico_config(request: Request):
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        try:
            interval = float(str(form.get("interval_minutes", "")).replace(",", "."))
            if interval <= 0:
                raise ValueError
        except ValueError:
            msg = _grp("err", "No se guardó", [], "el intervalo debe ser un número de minutos mayor que 0")
            return RedirectResponse(f"/historico?{urlencode({'message': msg})}", status_code=303)
        auth = str(form.get("auth", "service_account"))
        service.set_scan_log_cfg(
            enabled=form.get("enabled") is not None, interval_minutes=interval,
            profiles=[str(p) for p in form.getlist("profiles")] if form.get("all_profiles") is None else [],
            sheets_enabled=form.get("sheets_enabled") is not None, spreadsheet_id=str(form.get("spreadsheet_id", "")),
            auth=auth if auth in ("service_account", "oauth") else "service_account")
        msg = _grp("ok", "Guardado", [], "se aplica sin reiniciar")
        return RedirectResponse(f"/historico?{urlencode({'message': msg})}", status_code=303)

    @app.post("/historico/run")
    async def historico_run():
        summary = await service.scan_log_now()
        msg = _grp("ok", "Pasada hecha", [], f"{summary['profiles']} perfiles · {summary['appeared']} nuevos · {summary['vanished']} desaparecen")
        return RedirectResponse(f"/historico?{urlencode({'message': msg})}", status_code=303)
