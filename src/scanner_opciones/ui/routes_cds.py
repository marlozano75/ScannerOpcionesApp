"""Página del perfil «MTO CDS OTM» (Call Debit Spread muy OTM; ver docs/mto-spreads.md): filtros editables, perfiles CDS y resultados."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

from scanner_opciones.ui import cds_form
from scanner_opciones.ui.scanner_forms import profile_href

MAX_ROWS = 300


def register(app, service, render) -> None:
    @app.get("/cds", response_class=HTMLResponse)
    async def cds(request: Request):
        catalog = service.settings.scanner.cds
        profiles = service.profile_provider()[0] if service.profile_provider is not None else (service.stored_profiles() or ([], ""))[0]
        cds_profiles = [p for p in profiles if p.mode == "cds"]
        qp = request.query_params
        asked = qp.get("profile", "")
        active = next((p for p in cds_profiles if p.name == asked), None)
        if "submitted" not in qp:        # sin consulta: el perfil CDS pedido o el primero (si no hay ninguno, los valores de config.yaml)
            active = active or (cds_profiles[0] if cds_profiles else None)
            params = {**(active.set_params("cds") if active else {}), "submitted": "1", "profile": active.name if active else ""}
        else:
            params = dict(qp)
        parsed = cds_form.parse_cds(service, params)
        rows = []
        if catalog.enabled and parsed["cds"] is not None:
            rows = service.scan_cds(parsed["cds"], **parsed["ref"])
        saved = cds_form.parse_cds(service, {**active.set_params("cds"), "submitted": "1"})["form"] if active else None
        changed = sorted(k for k, v in parsed["form"].items() if str(saved.get(k)) != str(v)) if saved else []
        buttons = [dict(name=p.name, href=profile_href(p), on=active is not None and p.name == active.name, color=p.color) for p in cds_profiles]
        query = urlencode({**cds_form.form_query(parsed["form"]), "mode": "cds", "submitted": "1", "profile": active.name if active else ""})
        return render(request, "cds.html", no_autorefresh=True, watch_data=True, cfg=catalog, rows=rows[:MAX_ROWS], total=len(rows), f=parsed["form"],
                      parsed=parsed, buttons=buttons, active=active, changed=changed, save_query=query, widths_all=catalog.width_options)
