"""Pestaña Histórico: configuración del escaneo automático de perfiles y del volcado a Google Sheets, y el contenido registrado, repartido en tres
pestañas por fuente: Escaneos (registro de los perfiles de escaneo), Comunidad (libro apertura->cierre->P&L de las operaciones de Tortuga) y
MarketRadar (registro de las propuestas del radar). Escaneos y MarketRadar comparten el registro `scan_log` (aparición/desaparición); Comunidad
sale del libro `community_trades`."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scanner_opciones.jobs.scan_log_sheets import ACTIVE_HEADER, EVENTS_HEADER, active_row, event_row
from scanner_opciones.storage.scan_log import SCAN
from scanner_opciones.ui.common import MAX_FORM_FIELDS, _grp, message_groups

TABS = (("escaneos", "Escaneos"), ("comunidad", "Comunidad"), ("marketradar", "MarketRadar"))
SUBS = (("eventos", "Eventos"), ("activos", "Activos"))      # sub-vistas de Escaneos y MarketRadar (el registro `scan_log`)
KINDS = (("put", "Put"), ("spread", "Spread"))              # filtro Tipo de esas sub-vistas
LIMIT = 500      # filas que muestra la página (el Google Sheet recibe todas)

COMMUNITY_HEADER = ["Trader", "Ticker", "Estrategia", "Estado", "Apertura", "Cierre", "DTE abr.", "DTE cierre", "Días", "Crédito $",
                    "Coste cierre $", "P&L", "% capt.", "Moneda", "Nota"]


def _num(v, digits: int = 2):
    return "" if v is None else round(v, digits)


def _txt(v) -> str:
    return "" if v is None else str(v)


def community_row(t) -> list:
    """Una operación del libro de la comunidad (`TradeRow`) como fila de la tabla."""
    return [t.trader, t.ticker, t.strategy, "Cerrada" if t.status == "closed" else "Abierta", t.opened, _txt(t.closed),
            _txt(t.dte_open), _txt(t.dte_close), _txt(t.hold_days), _num(t.credit_open), _num(t.close_cost), _num(t.pnl),
            _num(t.pct_captured), _txt(t.currency), _txt(t.note)]


def register(app, service, render, remembered) -> None:
    @app.get("/historico", response_class=HTMLResponse)
    async def historico(request: Request, tab: str = "escaneos", sub: str = "eventos", kind: str = "", message: str = "", trader: str = ""):
        tab = tab if tab in {t for t, _ in TABS} else "escaneos"
        sub = sub if sub in {s for s, _ in SUBS} else "eventos"
        kind = kind if kind in {k for k, _ in KINDS} else ""
        repo, cfg = service.scan_log_repo, service.scan_log_cfg()
        job = service.scan_log
        writer = job.writer

        if tab == "comunidad":
            header = COMMUNITY_HEADER
            rows = [community_row(t) for t in service.community_trades.all(trader=trader or None, limit=LIMIT)]
            traders = service.community_trades.traders()
            cc = service.community_trades.counts()
            count_line = f"{cc['trades']} operaciones · {cc['closed']} cerradas · {cc['traders']} traders"
            kind = ""
        else:
            origin = SCAN if tab == "escaneos" else service.radar_feed.name
            if sub == "activos":
                header, rows = ACTIVE_HEADER, [active_row(r) for r in repo.all_active(origin, kind=kind or None)[:LIMIT]]
            else:
                header, rows = EVENTS_HEADER, [event_row(e) for e in repo.recent_events(LIMIT, origin, kind=kind or None)]
            traders = []
            trader = ""
            count_line = f"{repo.counts()['active']} contratos activos en total (todas las fuentes)"

        return render(request, "historico.html", no_autorefresh=True, watch_data=True, cfg=cfg, tab=tab, tabs=TABS, sub=sub, subs=SUBS,
                      kind=kind, kinds=KINDS, header=header, rows=rows, limit=LIMIT, count_line=count_line, profiles=[p[0] for p in service.profile_specs()],
                      last_run=job.last_run, last_summary=job.last_summary, trader=trader, traders=traders,
                      sheets_error=writer.last_error if writer is not None else None, pending=repo.counts()["pending"],
                      message=message, groups=message_groups(message))

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
