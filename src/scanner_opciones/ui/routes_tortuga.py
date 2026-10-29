"""Rutas de Ideas y de Tortuga (comunidad, radar y rutinas)."""
from __future__ import annotations

import logging
import asyncio
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from scanner_opciones.app.service import norm_ticker
from scanner_opciones.community.ideas import idea_key
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.ui.common import IDEAS_WAIT_SECONDS, MAX_FORM_FIELDS, _grp, message_groups, source_initials
from urllib.parse import urlencode

log = logging.getLogger(__name__)


def register(app, service, render, remembered) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/ideas", response_class=HTMLResponse)
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
                log.warning("Ideas: no se pudieron cotizar en directo", exc_info=True)
        everything = service.community_ideas() if data is not None else []
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
        tab_urls = {t: "/ideas?" + urlencode({k: v for k, v in (("venc", venc), ("trader", trader), ("tipo", t)) if v}) for t in counts}
        return render(request, "ideas.html", no_autorefresh=True, watch_data=True, nlv_usd=service.nlv_usd(), data=data, owned=service.owned_options(), held=service.held_tickers(), file_path=service.settings.community.path, views=views, total=len(base),
                      fit_cols=cols, counts=counts, tab_urls=tab_urls,
                      valid=sum(1 for v in views if v.status == "valid"), venc=venc, trader=trader, tipo=tipo,
                      traders=sorted({v.idea.trader for v in everything}, key=str.casefold), stale=service.community_stale(),
                      auto_reload=service.market.is_open(service.now()), quoting=quoting, message=message, groups=message_groups(message),
                      keys=[idea_key(v.idea) for v in views], perm_name=service.settings.community.perm_source_name,
                      lists={t: [(source_initials(n), n) for n in names] for t, names in service.ticker_lists().items()})

    # ---- Tortuga: radar de Bull Put Spreads y rutinas -------------------------------------------
    @app.get("/tortuga", response_class=HTMLResponse)
    async def tortuga(request: Request, cumple: int = 0, tipo: str = "", message: str = "", done: int = 0):
        runner = service.tortuga
        if done and not message:    # la página recargó sola al terminar una rutina: dice cómo acabó
            states = runner.state() if runner is not None else {}
            failed = [r.title for r in ROUTINES if states.get(r.key, {}).get("error")]
            message = (_grp("err", "Terminó con errores", failed, "mira la columna Resultado") if failed
                       else _grp("ok", "Terminada", [], "resultado actualizado"))
        data = service.radar_spreads()
        minimum = service.radar_min_yield()
        held = {norm_ticker(p.ticker) for p in service.state.positions}
        items = [dict(row=r, ok=radar_passes(r, minimum), held=norm_ticker(r.ticker) in held)
                 for r in (data.rows if data else ())]
        tipo = tipo if tipo in ("spread", "put") else ""
        shown = [i for i in items if (not cumple or i["ok"]) and (not tipo or i["row"].kind == tipo)]
        fits = service.radar_fits([i["row"] for i in shown])
        return render(request, "tortuga.html", no_autorefresh=True, watch_data=True, nlv_usd=service.nlv_usd(), data=data, items=shown, fits=fits,
                      fit_names=service.fit_names(), total=len(items), passing=sum(1 for i in items if i["ok"]),
                      minimum=minimum, cumple=cumple, tipo=tipo, cfg=service.settings.tortuga, message=message, groups=message_groups(message),
                      routines=ROUTINES, rstate=runner.state() if runner else {}, running=runner.running if runner else None,
                      enabled=runner is not None and service.settings.tortuga.enabled, busy_routines=service._tortuga_busy,
                      age_days=(service.now().date() - data.as_of).days if data and data.as_of else None)

    @app.get("/tortuga/status")
    async def tortuga_status():
        """¿Hay una rutina de Tortuga en marcha? La página de Tortuga lo consulta mientras espera para recargarse al terminar."""
        return {"busy": bool(service._tortuga_busy)}

    @app.post("/tortuga/run")
    async def tortuga_run(request: Request):
        """Lanza a mano una rutina de Tortuga (o todas las que toquen si no se indica) sin esperar a su hora."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        key = str(form.get("key", ""))
        if key not in {"all", *(r.key for r in ROUTINES)}:
            return Response("Rutina desconocida", status_code=400)
        started = service.tortuga_in_background(force=key)
        msg = _grp("ok", "Lanzada", [], "se está ejecutando en segundo plano; recarga la página en un par de minutos") if started else \
            _grp("skip", "No se ha lanzado", [], "ya hay una rutina en curso, no hay nada pendiente o las rutinas están desactivadas")
        return RedirectResponse(f"/tortuga?{urlencode({'message': msg})}", status_code=303)

    @app.post("/ideas/perm")
    async def ideas_perm(request: Request):
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
        return RedirectResponse(f"/ideas?{urlencode({**back, 'message': msg})}", status_code=303)
