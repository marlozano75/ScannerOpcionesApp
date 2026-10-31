"""Rutas de MarketRadar (antes «Tortuga»): radar de Tortuga (Bull Put Spreads y venta de puts) y sus rutinas."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from scanner_opciones.app.service import norm_ticker
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.ui.common import MAX_FORM_FIELDS, _grp, message_groups
from urllib.parse import urlencode


def register(app, service, render) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/tortuga")
    async def tortuga_old(request: Request):
        """Dirección anterior de la pestaña (marcadores guardados): redirige conservando los filtros."""
        return RedirectResponse("/marketradar" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)

    # ---- MarketRadar: radar de Tortuga (Bull Put Spreads y venta de puts) y rutinas -------------------------------------------
    @app.get("/marketradar", response_class=HTMLResponse)
    async def marketradar(request: Request, cumple: int = 0, tipo: str = "", message: str = "", done: int = 0):
        runner = service.tortuga
        if done and not message:    # la página recargó sola al terminar una rutina: dice cómo acabó
            states = runner.state() if runner is not None else {}
            failed = [r.title for r in ROUTINES if states.get(r.key, {}).get("error")]
            message = (_grp("err", "Terminó con errores", failed, "mira la columna Resultado") if failed
                       else _grp("ok", "Terminada", [], "resultado actualizado"))
        service.radar_quotes_in_background()
        data = service.radar_spreads()
        minimum, min_cw = service.radar_min_yield(), service.radar_min_credit_width()
        held = service.held_details()
        items = [dict(row=r, ok=radar_passes(r, minimum, min_cw), held=norm_ticker(r.ticker) in held, held_lines=held.get(norm_ticker(r.ticker), []))
                 for r in (data.rows if data else ())]
        base = [i for i in items if not cumple or i["ok"]]
        counts = {t: sum(1 for i in base if i["row"].kind == t) for t in ("put", "spread")}
        if tipo not in ("spread", "put"):      # sin elegir: PUT, salvo que solo haya spreads
            tipo = "spread" if counts["spread"] and not counts["put"] else "put"
        shown = [i for i in base if i["row"].kind == tipo]
        fits = service.radar_fits([i["row"] for i in shown], spread=tipo == "spread")
        specs, names = service.profile_specs(), service.fit_names()    # columnas de perfiles: solo los del instrumento elegido
        cols = [(i, s[0]) for i, s in enumerate(specs) if (s[2] if tipo == "spread" else s[1]) is not None] if specs else list(enumerate(names))
        cols = [(i, n.removesuffix(" (put)").removesuffix(" (spread)")) for i, n in cols]
        tab_urls = {t: "/marketradar?" + urlencode({k: v for k, v in (("cumple", cumple), ("tipo", t)) if v}) for t in counts}
        return render(request, "marketradar.html", no_autorefresh=True, watch_data=True, nlv_usd=service.nlv_usd(), data=data, items=shown, fits=fits,
                      fit_cols=cols, counts=counts, tab_urls=tab_urls, total=len(items), passing=sum(1 for i in items if i["ok"]),
                      minimum=minimum, min_cw=min_cw, cumple=cumple, tipo=tipo, cfg=service.settings.tortuga, message=message, groups=message_groups(message),
                      routines=ROUTINES, rstate=runner.state() if runner else {}, running=runner.running if runner else None,
                      enabled=runner is not None and service.settings.tortuga.enabled, busy_routines=service._tortuga_busy,
                      live_at=service.radar_live_at(), age_days=(service.now().date() - data.as_of).days if data and data.as_of else None)

    @app.get("/marketradar/status")
    async def marketradar_status():
        """¿Hay una rutina de Tortuga en marcha? La página de Tortuga lo consulta mientras espera para recargarse al terminar."""
        return {"busy": bool(service._tortuga_busy)}

    @app.post("/marketradar/run")
    async def marketradar_run(request: Request):
        """Lanza a mano una rutina de Tortuga (o todas las que toquen si no se indica) sin esperar a su hora."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        key = str(form.get("key", ""))
        if key not in {"all", *(r.key for r in ROUTINES)}:
            return Response("Rutina desconocida", status_code=400)
        started = service.tortuga_in_background(force=key)
        msg = _grp("ok", "Lanzada", [], "se está ejecutando en segundo plano; recarga la página en un par de minutos") if started else \
            _grp("skip", "No se ha lanzado", [], "ya hay una rutina en curso, no hay nada pendiente o las rutinas están desactivadas")
        return RedirectResponse(f"/marketradar?{urlencode({'message': msg})}", status_code=303)
