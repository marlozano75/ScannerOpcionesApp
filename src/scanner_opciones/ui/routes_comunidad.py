"""Rutas de Comunidad (antes «Ideas»): contratos de las fuentes valorados con los datos de hoy."""
from __future__ import annotations

import logging
import asyncio
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from scanner_opciones.community.ideas import idea_key
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.ui.common import IDEAS_WAIT_SECONDS, MAX_FORM_FIELDS, _grp, message_groups, source_initials
from urllib.parse import urlencode

log = logging.getLogger(__name__)


def register(app, service, render, remembered) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/ideas")
    async def ideas_old(request: Request):
        """Dirección anterior de la pestaña (marcadores guardados): redirige conservando los filtros."""
        return RedirectResponse("/comunidad" + (f"?{request.url.query}" if request.url.query else ""), status_code=307)

    @app.get("/comunidad", response_class=HTMLResponse)
    async def ideas(request: Request, venc: int = 0, trader: str = "", tipo: str = "put", message: str = ""):
        data = service.community()
        quoting = False
        task = service.community_quotes_task() if data is not None else None
        if task is not None:      # espera lo razonable; si tarda más, la página sale con lo que hay y se recarga sola cuando termine
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=IDEAS_WAIT_SECONDS)
            except asyncio.TimeoutError:
                quoting = True
            except Exception:
                log.warning("Comunidad: no se pudieron cotizar en directo", exc_info=True)
        me = service.settings.community.my_trader.casefold()      # tus operaciones no se muestran (siguen sirviendo al Panel para reconocer el perfil)
        everything = [v for v in service.community_ideas() if v.idea.source == service.community_feed.name and (not me or (v.idea.trader or "").casefold() != me)] if data is not None else []
        tipo = tipo if tipo in ("put", "spread") else "put"
        base = [v for v in everything
                if (venc or v.status != "expired") and (not trader or v.idea.trader == trader)]
        is_spread = lambda v: v.idea.long_strike is not None
        counts = {"put": sum(1 for v in base if not is_spread(v)), "spread": sum(1 for v in base if is_spread(v))}
        views = [v for v in base if is_spread(v) == (tipo == "spread")]
        # Columnas de perfiles: solo los del instrumento elegido (cada perfil es de put o de spread); (posición en `fits`, nombre sin sufijo)
        specs, names = service.profile_specs(), service.fit_names()
        cols = [(i, s[0]) for i, s in enumerate(specs) if (s[2] if tipo == "spread" else s[1]) is not None] if specs else list(enumerate(names))
        cols = [(i, n.removesuffix(" (put)").removesuffix(" (spread)")) for i, n in cols]
        tab_urls = {t: "/comunidad?" + urlencode({k: v for k, v in (("venc", venc), ("trader", trader), ("tipo", t)) if v}) for t in counts}
        return render(request, "comunidad.html", no_autorefresh=True, watch_data=True, nlv_usd=service.nlv_usd(), data=data, owned=service.owned_options(), held=service.held_tickers(), file_path=service.settings.community.path, views=views, total=len(base),
                      fit_cols=cols, counts=counts, tab_urls=tab_urls,
                      valid=sum(1 for v in views if v.status == "valid"), venc=venc, trader=trader, tipo=tipo,
                      traders=sorted({v.idea.trader for v in everything if v.idea.trader}, key=str.casefold), stale=service.community_stale(),
                      auto_reload=service.market.is_open(service.now()), quoting=quoting, message=message, groups=message_groups(message),
                      keys=[idea_key(v.idea) for v in views], perm_name=service.settings.community.perm_source_name,
                      lists={t: [(source_initials(n), n) for n in names] for t, names in service.ticker_lists().items()})

    @app.get("/comunidad/feed", response_class=HTMLResponse)
    async def comunidad_feed(request: Request, trader: str = "", estado: str = ""):
        """Feed de la comunidad: operaciones que los traders comparten (cualquier estrategia, abiertas y cerradas con P&L y su tesis)."""
        feed = service.community_feed_trades()
        community = service.community()        # para el chip de fiabilidad de cada trader
        me = service.settings.community.my_trader.casefold()
        everything = [t for t in (feed.trades if feed is not None else []) if not me or t.trader.casefold() != me]
        estado = estado if estado in ("open", "closed") else ""
        views = [t for t in everything if (not trader or t.trader == trader) and (not estado or t.status == estado)]
        counts = {"open": sum(1 for t in everything if t.status == "open"), "closed": sum(1 for t in everything if t.status == "closed")}
        return render(request, "feed.html", no_autorefresh=True, watch_data=True, data=feed, community=community,
                      views=views, total=len(everything), counts=counts,
                      trader=trader, estado=estado, file_path=service.settings.community.feed_path,
                      traders=sorted({t.trader for t in everything}, key=str.casefold),
                      auto_reload=service.market.is_open(service.now()))

    @app.get("/comunidad/estilo", response_class=HTMLResponse)
    async def comunidad_estilo(request: Request, trader: str = ""):
        """Estilo de salida de cada trader a partir del historial apertura->cierre que se ha ido acumulando del Feed (operaciones de crédito cerradas)."""
        styles = service.trader_exit_styles()
        community = service.community()        # para el chip de fiabilidad de cada trader
        counts = service.community_trades.counts()
        views = [s for s in styles if not trader or s.trader == trader]
        rows = service.community_trades.all(trader=trader, status="closed") if trader else []
        return render(request, "estilo.html", no_autorefresh=True, watch_data=True, community=community,
                      styles=views, counts=counts, trader=trader, rows=rows,
                      traders=[s.trader for s in styles], file_path=service.settings.community.feed_path,
                      auto_reload=service.market.is_open(service.now()))

    @app.post("/comunidad/perm")
    async def comunidad_perm(request: Request):
        """Añade a la fuente permanente del Universo los tickers de las ideas marcadas (con su trader y su mejor estrategia)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        back = {k: str(form.get(k)) for k in ("venc", "trader", "tipo") if form.get(k)}
        try:
            res = await service.add_community_perm([str(k) for k in form.getlist("idea")])
        except WatchlistError as exc:
            msg = _grp("err", "Error", [], str(exc))
        else:
            name, parts = res["source"], []
            if res["added"]:
                parts.append(_grp("ok", f"Añadidos a {name}", [f"{t} · {res['perm'][t]['strategy']} {res['perm'][t]['passed']}/{res['perm'][t]['total']}"
                                                             if res["perm"].get(t, {}).get("strategy") else t for t in res["added"]]))
            if res["already"]:
                parts.append(_grp("skip", "Ya estaban (trader y estrategia actualizados)", list(res["already"])))
            if res["no_options"]:
                parts.append(_grp("warn", "Sin opciones (no añadidos)", res["no_options"]))
            if not res["checked"] and res["added"]:
                parts.append(_grp("warn", "Aviso", [], "no se pudo comprobar si tienen opciones"))
            msg = "\n".join(parts) or _grp("skip", "Nada que añadir", [], "")
            remembered.pop("universe", None)
        return RedirectResponse(f"/comunidad?{urlencode({**back, 'message': msg})}", status_code=303)
