"""Rutas del Universo: fuentes, ficheros, tickers manuales, sincronización de la Watchlist y periodicidad."""
from __future__ import annotations

import logging
import tempfile
from dataclasses import replace
from datetime import datetime
from fastapi import Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pathlib import Path
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.rankedstocks.loader import SYMBOL_HEADERS, _plain
from scanner_opciones.scanner.quality import is_exempt, ticker_quality_reject
from scanner_opciones.tortuga.schedule import FeedSchedule, ScheduleError, describe as describe_schedule, parse_times
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.ui.common import MAX_FORM_FIELDS, _grp, _load_groups, message_groups
from scanner_opciones.ui.scanner_forms import SOLVENCY_UI, _fmt, _sector_of, quality_form, read_quality, solvency_controls
from scanner_opciones.universe.descriptions import SourceInfo, describe, describe_manual
from scanner_opciones.universe.hellostocks_html import HTML_SUFFIXES, missing_strategies
from scanner_opciones.universe.sources import ALL, load_sources, merge
from scanner_opciones.watchlist.parser import parse_text
from urllib.parse import urlencode

log = logging.getLogger(__name__)


def register(app, service, render, recall, remembered) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/universe", response_class=HTMLResponse)
    async def universe(request: Request, src: str = ALL, message: str = "", view: str = "", done: int = 0):
        runner = service.tortuga
        if done and not message:    # la página recargó sola al terminar una rutina: dice cómo acabó
            states = runner.state() if runner is not None else {}
            failed = [r.title for r in ROUTINES if states.get(r.key, {}).get("error")]
            message = (_grp("err", "Terminó con errores", failed, "mira la columna Resultado") if failed
                       else _grp("ok", "Terminada", [], "resultado actualizado"))
        if (back := recall(request, "universe", ignore=("message", "debug", "view", "done"))) is not None:
            return back
        sources = service.universe_sources
        if view == "history":
            history = [{**h, "at": datetime.fromisoformat(h["at"])} for h in service.universe_history if h.get("at")]
            return render(request, "universe.html", no_autorefresh=True, table=None, rows=[], message=message, groups=message_groups(message), history=history,
                          view="history", src=ALL, sources=sources, numbers={s.name: n for n, s in enumerate(sources, 1)},
                          all_count=len({r.ticker for s in sources for r in s.table.rows}),
                          manual=[(n, len(t)) for n, t in service.manual_sources.items()])
        numbers = {s.name: n for n, s in enumerate(sources, 1)}           # número de cada fuente
        current = next((s for s in sources if s.name == src), None)
        merged = merge(sources) if sources else None
        table = current.table if current else merged
        member: dict[str, list[int]] = {}
        for s_ in sources:
            for r in s_.table.rows:
                member.setdefault(r.ticker, []).append(numbers[s_.name])
        rows = list(table.rows) if table is not None else []
        # filtros de calidad de la empresa (arriba de la tabla): se aplican a las filas; las que no pasan no se muestran
        qcfg = service.settings.scanner.quality
        qinfos = service.quality.all()
        ident = service.universe_identity()                               # ticker -> (empresa, sector) de todo el Universo
        sectors = {t: i.sector for t, i in service.ticker_info.all().items()}
        sector_col = next((i for i, c in enumerate(table.columns) if c.name.strip().lower() == "sector"), None) if table else None
        base = service.criteria()
        form, overrides, filter_error = quality_form(base), {}, None
        try:
            read_quality(request.query_params, form, overrides, qcfg, earnings=False, liquidity=False, solvency=True)
        except ValueError as exc:
            form, overrides, filter_error = quality_form(base), {}, f"Filtro no válido: {exc}"
        criteria = base.with_filters(**overrides)
        total_rows = len(rows)
        exempt_tickers: set[str] = set()
        wl_mode = service.watchlist_sync_mode()
        wl_plan = service.wl_sync_held or (service.watchlist_sync_plan() if wl_mode == "" else None)      # resumen previo la primera vez
        wl_sync = {"plan": wl_plan, "held": service.wl_sync_held is not None, "grace": service.settings.watchlist_sync.grace_days} if wl_plan is not None and (service.wl_sync_held or not wl_plan.empty) else None
        exempt_entries = service.quality_exempt()
        shown = []
        for row in rows:
            info = replace(qinfos.get(row.ticker) or TickerInfo(row.ticker),
                           sector=_sector_of(row, sector_col, sectors) or ident.get(row.ticker, ("", ""))[1] or None)
            if is_exempt(info, exempt_entries):
                exempt_tickers.add(row.ticker)
            if ticker_quality_reject(info, criteria, exempt_entries) is None:
                shown.append(row)
        rows = shown
        labels = {key: label for key, _, _, label, _, _ in SOLVENCY_UI}
        q_presets = [dict(name=p.name, levels=p.levels, manage=p.manageable_debt,
                          title=" · ".join([f"{labels[k]}: {qcfg.level_labels[v]}" for k, v in p.levels.items()]
                                           + (["Deuda baja o manejable"] if p.manageable_debt else [])),
                          is_on=all(form.get(k) == v for k, v in p.levels.items()) and form.get("q_manage") == p.manageable_debt
                          and bool(p.levels or p.manageable_debt)) for p in qcfg.presets]
        n_active = sum(1 for k, v in form.items() if v and k != "q_earn")
        with_quality = sum(1 for r in rows if r.ticker in qinfos)
        active = {s.name for s in sources}
        files = [(name, at, [s.name for s in srcs if s.name in active]) for name, (at, srcs) in service.universe_files.items()]
        info = None
        if current is not None:
            feed_note = service.feed_source_note(current.name)
            info = SourceInfo(current.name, feed_note) if feed_note else describe_manual(current.name) if current.name in service.manual_sources \
                else describe(current.name, current.criteria)
        identity_cols = {"company", "empresa", "sector"} | set(SYMBOL_HEADERS)   # van delante: no se repiten al final
        skip = {i for i, c in enumerate(table.columns) if _plain(c.name) in identity_cols} if table else set()
        return render(request, "universe.html", no_autorefresh=True, table=table, rows=rows, message=message, groups=message_groups(message),
                      ident=ident, skip=skip, wl_sync=wl_sync,
                      qp=request.query_params, src=current.name if current else ALL, sources=sources, files=files,
                      numbers=numbers, member={t: ", ".join(map(str, n)) for t, n in member.items()},
                      all_count=len(member), manual=[(n, len(t)) for n, t in service.manual_sources.items()],
                      source_info=info, in_watchlist=set(service.watchlist.list()),
                      qinfos=qinfos, with_quality=with_quality, edgar=bool(service.settings.edgar.contact.strip()),
                      exempt_tickers=exempt_tickers, exempt_names=", ".join(qcfg.exempt_sectors),
                      feeds=service.feed_rows(), describe=describe_schedule,
                      routines=ROUTINES, rstate=runner.state() if runner else {}, running=runner.running if runner else None,
                      enabled=runner is not None and service.settings.tortuga.enabled, busy_routines=service._tortuga_busy, cfg=service.settings.tortuga,
                      renamable=bool(current) and service.is_renamable_source(current.name),
                      total_rows=total_rows, qform=form, filter_error=filter_error, n_active=n_active, q_presets=q_presets,
                      solvency=solvency_controls(qcfg, form), level_names=qcfg.level_labels, manageable=qcfg.manageable_debt,
                      quality_opts=dict(quarters=qcfg.positive_quarters_options,
                                        leverage=[_fmt(v) for v in qcfg.leverage_options]))

    @app.post("/universe/load")
    async def universe_load(files: list[UploadFile]):
        done, errors, notes, detail = [], [], [], []
        log.info("Universo: petición de carga con %d fichero(s): %s", len(files), [f.filename for f in files])
        for upload in files:
            name = Path(upload.filename or "").name
            if not name:
                log.warning("Universo: fichero sin nombre, se ignora")
                continue
            content = await upload.read()
            log.info("Universo: recibido %s (%d bytes)", name, len(content))
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
                tmp.write(content)
                path = Path(tmp.name)
            try:
                sources = load_sources(path, name)
                if path.suffix.lower() in HTML_SUFFIXES:        # página de HelloStocks: avisa de listas sin descargar
                    notes += [f"{name}: {w}" for w in missing_strategies(path)]
                raw_by_source = {src.name: [r.ticker for r in src.table.rows] for src in sources}
                sources, removed, checked = await service.prune_without_options(sources)
            except WatchlistError as exc:
                log.warning("Universo: %s no se pudo cargar: %s", name, exc)
                errors.append(f"{name}: {exc}")
                continue
            except Exception:
                log.exception("Universo: error inesperado al cargar %s", name)
                raise
            finally:
                path.unlink(missing_ok=True)
            log.info("Universo: %s -> %d fuente(s), %d ticker(s) sin opciones descartados, comprobado=%s",
                     name, len(sources), removed, checked)
            if not sources:
                errors.append(f"{name}: ningún ticker tiene opciones")
                continue
            detail += _load_groups(raw_by_source, sources, {s_.name: [r.ticker for r in s_.table.rows] for s_ in service.universe_sources})
            service.set_universe_file(name, sources, content)   # queda guardado hasta que se quite o se cargue otro igual
            done.append((name, f"{name}: {len(sources)} fuente{'s' if len(sources) != 1 else ''}, {sum(len(s.table.rows) for s in sources)} filas"
                         + (f", {removed} tickers sin opciones descartados" if removed else "")))
            if not checked:
                notes.append("no se pudo comprobar qué tickers tienen opciones; se han cargado todos")
        remembered.pop("universe", None)   # otras columnas: los filtros anteriores no valen
        groups = []
        if done:
            groups.append(_grp("ok", "Cargado", [d[0] for d in done], "; ".join(d[1] for d in done if d[1])))
        groups += detail
        if notes:
            groups.append(_grp("warn", "Aviso", [], "; ".join(dict.fromkeys(notes))))
        if errors:
            groups.append(_grp("err", "Error", [], "; ".join(errors)))
        msg = "\n".join(groups)
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    @app.post("/universe/manual")
    async def universe_manual(text: str = Form(""), source: str = Form(""), mode: str = Form("add")):
        """Añade tickers escritos a mano a una fuente con nombre (se crea si no existe) o la sustituye (`mode=replace`)."""
        parsed = parse_text(text)
        log.info("Universo: tickers manuales recibidos para «%s» (%s): %s", source, mode, parsed.tickers)
        try:
            res = await service.add_manual_tickers(parsed, source, replace_source=mode == "replace")
        except WatchlistError as exc:
            return RedirectResponse(f"/universe?{urlencode({'message': _grp('err', 'Error', [], str(exc))})}", status_code=303)
        parts = []
        src_name = res["source"]
        if res["removed"]:
            parts.append(_grp("del", f"Quitados de {src_name}", res["removed"]))
        if res["added"]:
            fresh = len(res["new_in_universe"])
            tail = f"{fresh} nuevo{'s' if fresh != 1 else ''} en el Universo"                    + (", el resto ya estaban en otras fuentes" if fresh != len(res["added"]) - len(res["kept"]) else "")
            if mode == "replace":
                fresh_list = [t for t in res["added"] if t not in res["kept"]]
                if fresh_list:
                    parts.append(_grp("ok", f"Nuevos en {src_name}", fresh_list, tail))
                parts.append(_grp("skip", f"Conservados en {src_name}", [], f"{len(res['kept'])} ticker{'s' if len(res['kept']) != 1 else ''} que ya estaban"))
            else:
                parts.append(_grp("ok", f"Añadidos a {src_name}", res["added"], tail))
        if res["already"]:
            parts.append(_grp("skip", "Ya incluidos", [f"{t} ({' · '.join(n)})" for t, n in res["already"].items()]))
        if res["no_options"]:
            parts.append(_grp("warn", "Sin opciones (no añadidos)", res["no_options"]))
        if parsed.rejected:
            parts.append(_grp("err", "Rechazados", [t for t, _ in parsed.rejected]))
        if not res["checked"] and res["added"]:
            parts.append(_grp("warn", "Aviso", [], "no se pudo comprobar si tienen opciones"))
        msg = "\n".join(parts) or _grp("skip", "Nada que añadir", [], "no hay ningún ticker que añadir")
        remembered.pop("universe", None)
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    @app.post("/universe/watchlist-sync")
    async def universe_watchlist_sync(request: Request):
        """Decisión tras el resumen previo: la Watchlist sigue al Universo (`on=1`, aplica el plan ya) o se gestiona a mano (`on=0`)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        on = str(form.get("on", "")) == "1"
        plan = await service.set_watchlist_sync(on)
        remembered.pop("universe", None)
        if on:
            msg = _grp("ok", "Watchlist sincronizada con el Universo", [], f"{len(plan.add)} añadidos (actualización diaria en segundo plano), {len(plan.remove)} quitados (los que no tienen fuente se quitan tras {service.settings.watchlist_sync.grace_days} días)")
        else:
            msg = _grp("skip", "Watchlist a mano", [], "no se cambia nada solo; el resumen no vuelve a salir")
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    # ---- periodicidad de las fuentes API / MCP (Universo) ----------------------------------------------------
    @app.post("/universe/schedule")
    async def universe_schedule(request: Request):
        """Periodicidad de las fuentes API/MCP (campos `in_<clave>`, `out_<clave>`, `times_<clave>`, `close_<clave>`)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        try:
            schedules = {}
            for feed in service.feed_rows():
                k = feed["key"]
                schedules[k] = FeedSchedule.from_json({"in_market": form.get(f"in_{k}") or None, "out_market": form.get(f"out_{k}") or None,
                                                      "times": parse_times(str(form.get(f"times_{k}") or "")), "after_close": form.get(f"close_{k}") == "1"})
            service.set_feed_schedules(schedules)
            msg = _grp("ok", "Periodicidad guardada", [], "se aplica en el próximo ciclo de refresco")
        except ScheduleError as exc:
            msg = _grp("err", "No se ha guardado la periodicidad", [], str(exc))
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    @app.post("/universe/schedule/reset")
    async def universe_schedule_reset():
        service.reset_feed_schedules()
        return RedirectResponse(f"/universe?{urlencode({'message': _grp('ok', 'Periodicidad restaurada', [], 'vuelve a lo de config.yaml')})}", status_code=303)

    @app.post("/universe/source/rename")
    async def universe_source_rename(src: str = Form(""), new_name: str = Form("")):
        """Cambia el nombre de una fuente manual."""
        try:
            final = service.rename_manual_source(src, new_name)
        except WatchlistError as exc:
            return RedirectResponse(f"/universe?{urlencode({'message': _grp('err', 'Error', [], str(exc))})}", status_code=303)
        remembered.pop("universe", None)
        msg = _grp("ok", f"Fuente renombrada: {src} → {final}", [], "")
        return RedirectResponse(f"/universe?{urlencode({'message': msg, 'src': final})}", status_code=303)

    @app.post("/universe/source/remove")
    async def universe_source_remove(request: Request):
        """Quita de una fuente los tickers marcados (`scope=selected`) o todos (`scope=all`)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        source, scope = str(form.get("src", "")), str(form.get("scope", "selected"))
        try:
            n = service.remove_source(source) if scope == "all" else                 service.remove_source_tickers(source, [str(t) for t in form.getlist("sel")])
        except WatchlistError as exc:
            return RedirectResponse(f"/universe?{urlencode({'message': _grp('err', 'Error', [], str(exc))})}", status_code=303)
        remembered.pop("universe", None)
        msg = _grp("del", f"Quitados de {source}", [], f"{n} ticker{'s' if n != 1 else ''}")
        still = any(s.name == source for s in service.universe_sources)
        query = {"message": msg, **({"src": source} if still else {})}
        return RedirectResponse(f"/universe?{urlencode(query)}", status_code=303)

    # ---- scanner y simulador ---------------------------------------------------------------

    @app.get("/universe/routines/status")
    async def universe_routines_status():
        """¿Hay una rutina de Tortuga en marcha? La página del Universo lo consulta mientras espera para recargarse al terminar."""
        return {"busy": bool(service._tortuga_busy)}

    @app.post("/universe/routines/run")
    async def universe_routines_run(request: Request):
        """Lanza a mano una rutina de Tortuga (o todas las que toquen si no se indica) sin esperar a su hora."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        key = str(form.get("key", ""))
        if key not in {"all", *(r.key for r in ROUTINES)}:
            return Response("Rutina desconocida", status_code=400)
        started = service.tortuga_in_background(force=key)
        msg = _grp("ok", "Lanzada", [], "se está ejecutando en segundo plano; recarga la página en un par de minutos") if started else \
            _grp("skip", "No se ha lanzado", [], "ya hay una rutina en curso, no hay nada pendiente o las rutinas están desactivadas")
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)
