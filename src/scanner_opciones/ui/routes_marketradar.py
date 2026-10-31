"""Rutas de MarketRadar (antes «Tortuga»): radar de Tortuga (Bull Put Spreads y venta de puts) (sus rutinas de lectura están en el Universo)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from scanner_opciones.app.service import norm_ticker
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.ui.common import message_groups
from urllib.parse import urlencode


def register(app, service, render) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/tortuga")
    async def tortuga_old(request: Request):
        """Dirección anterior de la pestaña (marcadores guardados): redirige conservando los filtros."""
        return RedirectResponse("/marketradar" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)

    # ---- MarketRadar: radar de Tortuga (Bull Put Spreads y venta de puts) y rutinas -------------------------------------------
    @app.get("/marketradar", response_class=HTMLResponse)
    async def marketradar(request: Request, cumple: int = 0, tipo: str = "", message: str = ""):
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
                      enabled=service.tortuga is not None and service.settings.tortuga.enabled,
                      live_at=service.radar_live_at(), age_days=(service.now().date() - data.as_of).days if data and data.as_of else None)
