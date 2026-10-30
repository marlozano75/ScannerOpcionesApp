"""Interfaz web local (FastAPI + Jinja2). Sin lógica de negocio: solo llama al AppService."""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from urllib.parse import parse_qsl, urlencode
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape

from pydantic import ValidationError

from scanner_opciones.app.service import SCANNER_USER_CONFIG, AppService, SelectedContract, norm_ticker
from scanner_opciones.marketdata.analysts import summary
from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.domain.enums import AccountMode, OptionRight, Platform, PriceReference, TrafficLight
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.rankedstocks.loader import clean_ticker
from scanner_opciones.community.ideas import idea_key
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.technical import strike_history
from scanner_opciones.scanner.currency import usd_rate
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, unavailable_ma_fields
from scanner_opciones.portfolio import exits as exits_mod
from scanner_opciones.portfolio.management import manage_puts
from scanner_opciones.ui.position_charts import exit_plan, position_chart, position_mini
from scanner_opciones.scanner.engine import ScanOutput
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.tortuga.schedule import FeedSchedule, ScheduleError, describe as describe_schedule, parse_times
from scanner_opciones.scanner.impact import explain_empty
from scanner_opciones.portfolio.classify import FALLBACK as FALLBACK_PROFILE, dte_range
from scanner_opciones.scanner.profiles import Profile, profile_diff
from scanner_opciones.scanner.profile_marks import row_key, row_marks
from scanner_opciones.scanner.market import vix_advice
from scanner_opciones.scanner.quality import is_exempt, ticker_quality_reject
from scanner_opciones.portfolio.overview import other_positions, portfolio_status
from scanner_opciones.ui import dashboard_charts as dash, page_charts, routes_historico, routes_tortuga, routes_universe, scanner_parse, viz
from scanner_opciones.ui.charts import strike_chart_html, strike_mini_svg
from scanner_opciones.rankedstocks.loader import SYMBOL_HEADERS, _plain
from scanner_opciones.universe.descriptions import describe, describe_manual
from scanner_opciones.universe.hellostocks_html import HTML_SUFFIXES, missing_strategies
from scanner_opciones.universe.sources import ALL, load_sources, merge
from scanner_opciones.watchlist.parser import parse_text, parse_tokens

log = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def own_qty(owned, ticker, expiry, strike):
    """Contratos que ya tienes de esa put (negativo = vendidos, positivo = comprados) o None; lo usan las plantillas."""
    return (owned or {}).get((norm_ticker(ticker), expiry, round(float(strike), 4)))


TEMPLATES.env.globals["own_qty"] = own_qty


def held_note(held, ticker, expiry, strike):
    """(texto corto, detalle) de lo que tienes del subyacente aparte de la put exacta de la fila (acciones, calls y otras puts), o None."""
    entry = (held or {}).get(norm_ticker(ticker))
    if not entry:
        return None
    exact = (OptionRight.PUT, expiry, round(float(strike), 4))
    shares = entry["shares"]
    puts = sum(q for (r, *_), q in entry["options"].items() if r is OptionRight.PUT) - entry["options"].get(exact, 0.0)
    calls = sum(q for (r, *_), q in entry["options"].items() if r is OptionRight.CALL)
    short, long = [], []
    if shares:
        short.append("ACC")
        long.append(f"{int(shares)} acciones")
    if puts:
        short.append("P")
        long.append(f"{int(abs(puts))} put{'s' if abs(puts) != 1 else ''} {'vendida' if puts < 0 else 'comprada'}{'s' if abs(puts) != 1 else ''} (otro strike o vencimiento)")
    if calls:
        short.append("C")
        long.append(f"{int(abs(calls))} call{'s' if abs(calls) != 1 else ''} {'vendida' if calls < 0 else 'comprada'}{'s' if abs(calls) != 1 else ''}")
    if not short:
        return None
    lines = entry.get("lines")      # detalle de cada posición o combo (tipo, strikes, vencimientos); sin él, el resumen por tipo
    return (" ".join(short), "Ya tienes en cartera:" + "".join(f"\n• {l}" for l in lines) if lines else "Ya tienes en cartera: " + "; ".join(long))


TEMPLATES.env.globals["held_note"] = held_note


def sec_flag(alerts, ticker):
    """⚠ junto al ticker si tiene presentaciones recientes ante la SEC de las que preceden a problemas (cuentas no fiables, cambio de auditor,
    exclusión de bolsa...); el texto de cada una sale al pasar el ratón."""
    items = (alerts or {}).get(norm_ticker(ticker))
    if not items:
        return ""
    tip = "Presentaciones recientes ante la SEC (mira antes de vender una put):\n" + "\n".join(a.text for a in items)
    return Markup(f' <span class="sec" title="{escape(tip)}">⚠</span>')


TEMPLATES.env.globals["sec_flag"] = sec_flag


@pass_context
def tk_title(ctx, names, ticker, extra=""):
    """Texto al pasar el ratón por un ticker: el nombre completo de la acción o ETF (si se conoce), `extra` y, debajo, a qué se dedica la empresa
    y sus noticias recientes con fecha (`company_notes` del contexto de la página)."""
    key = norm_ticker(ticker)
    note = (ctx.get("company_notes") or {}).get(key, "")
    return "\n".join(p for p in ((names or {}).get(key, ""), extra, note) if p)


TEMPLATES.env.globals["tk_title"] = tk_title
TEMPLATES.env.globals["fx"] = usd_rate     # dólares por unidad de la moneda de las opciones de un ticker (1 si cotizan en USD)


def analyst_flag(changes, ticker):
    """▼ (bajadas de rating) o ▲ (subidas) de analistas en los últimos días junto al ticker; el detalle de cada cambio sale al pasar el ratón."""
    items = (changes or {}).get(norm_ticker(ticker))
    if not items:
        return ""
    mark, css, short = summary(items)
    items = sorted(items, key=lambda c: c.action != "down")      # las bajadas de rating primero (sorted es estable: siguen de más reciente a más antigua)
    lines = [c.text for c in items[:12]] + ([f"… y {len(items) - 12} más"] if len(items) > 12 else [])
    tip = "\n".join([f"Analistas, últimos días: {short}", *lines])
    return Markup(f' <span class="an {css}" title="{escape(tip)}">{mark}</span>')


TEMPLATES.env.globals["analyst_flag"] = analyst_flag
LIGHT_LABEL = {
    TrafficLight.GREEN: "Normal / Holgado",
    TrafficLight.AMBER: "Preocupación",
    TrafficLight.ORANGE: "Riesgo elevado",
    TrafficLight.RED: "Riesgo alto",
    TrafficLight.UNKNOWN: "Sin datos",
}


def _pct(v: Optional[float], digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.0f}"


def _signed(v: Optional[float], digits: int = 0) -> str:
    """Importe con signo y miles: «+1,234» / «−56» (el signo menos tipográfico); «—» sin dato."""
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else '−'}{abs(v):,.{digits}f}"


def _x(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.2f}x"


def _num(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _days_label(days: int) -> str:
    """7 -> «1 semana», 30 -> «1 mes», 365 -> «1 año», 252 -> «252 días (~1 año bursátil)»; otros valores, «N días»."""
    if days == 252:
        return "252 días (~1 año bursátil)"
    for size, one, many in ((365, "año", "años"), (30, "mes", "meses"), (7, "semana", "semanas")):
        if days % size == 0:
            n = days // size
            return f"{n} {one if n == 1 else many}"
    return f"{days} días"


from scanner_opciones.ui.common import IDEAS_WAIT_SECONDS, MAX_FORM_FIELDS, _grp, _load_groups, message_groups, source_initials  # noqa: F401  (los tests y las rutas que quedan)
from scanner_opciones.ui.scanner_forms import (  # noqa: F401  (se vuelven a exportar: los tests importan desde web)
    QUALITY_SELECTS, SEED_COLORS, SOLVENCY_CORE, SOLVENCY_UI, SPREAD_FIELDS, STRATEGY_FIELDS,
    STRATEGY_KEYS, STRUCT_KEYS, VIX_FIELDS, _count_text, _fmt, _opt_float,
    _read_num, _required, _sector_of, _threshold_text, parse_scanner_config, parse_spread_config,
    preset_link, profile_link, quality_counts, quality_form, read_quality, seed_fallback,
    seed_profiles, solvency_controls, split_sets, strategy_link,
)


TEMPLATES.env.globals["viz"] = viz
TEMPLATES.env.filters.update(
    pct=_pct, money=_money, signed=_signed, num=_num, x=_x, light_label=lambda l: LIGHT_LABEL[l], zip=lambda a, b: zip(a, b)
)


def create_app(
    service: AppService,
    gateway_factory: Optional[Callable[[AccountMode], BrokerGateway]] = None,
    on_startup: Optional[Callable[[], Awaitable[None]]] = None,
    on_shutdown: Optional[Callable[[], Awaitable[None]]] = None,
) -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if on_startup:
            await on_startup()
        yield
        if on_shutdown:
            await on_shutdown()

    app = FastAPI(title="ScannerOpcionesApp", lifespan=lifespan)

    symbols = {"EUR": "€", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}
    quote_symbols = {t: symbols.get(i.currency, i.currency + " ") for t, i in service.settings.instruments.items() if i.currency != "USD"}
    TEMPLATES.env.globals["ccy"] = lambda ticker: quote_symbols.get(ticker, "")     # moneda de los importes de las opciones que no son USD («€»)

    remembered: dict[str, str] = {}   # última consulta con filtros de cada pestaña (en memoria)

    def remember(page: str, query: Optional[str]) -> None:
        if query is None:
            remembered.pop(page, None)
        else:
            remembered[page] = query

    def recall(request: Request, page: str, ignore: tuple[str, ...] = ("message", "debug")) -> Optional[RedirectResponse]:
        """Los filtros viajan en la URL, así que al volver a una pestaña desde el menú (sin parámetros)
        se recuperan los últimos usados. `?reset=1` los descarta y muestra los valores iniciales."""
        qp = request.query_params
        if "reset" in qp:
            remember(page, None)
            return None
        kept = [(k, v) for k, v in qp.multi_items() if k not in ignore]
        if kept:
            remember(page, urlencode(kept))
            return None
        if page in remembered and not qp:
            return RedirectResponse(f"/{page}?{remembered[page]}", status_code=303)
        return None

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        base = dict(
            state=service.state, mode=service.settings.ibkr.mode.value, platform=service.settings.ibkr.platform.value,
            endpoints=service.endpoints,
            account_id=(service.state.account.account_id if service.state.account else service.settings.ibkr.account),
            account_alias=service.state.account.alias if service.state.account else None, busy=service.busy,
            refresh_minutes=service.settings.refresh_interval_minutes,
            # el aviso de datos diferidos solo vale si las cotizaciones salen de IBKR; con tastytrade (cotizaciones, precios, IV y VIX en vivo) confundiría
            delay_minutes=service.settings.ibkr.delay_minutes if service.settings.market_data.source == "ibkr" else 0,
            pacing_wait=service.pacing_wait_seconds() if service.busy else 0,
            market_closed=service.settings.market.pause_when_closed and not service.market_open(),
            next_open=service.market.next_open(service.now()), path=request.url.path,
            excluded_notice=service.excluded_notice, sec_alerts=service.filing_alert_map(), analyst_alerts=service.analyst_map(), company_names=service.company_name_map(),
            company_notes=service.company_notes_map(),
        )
        return TEMPLATES.TemplateResponse(request, name, {**base, **ctx})

    # ---- panel -----------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        sectors, weeks = service.diversification()
        assignment, risk, vix = service.assignment(), service.state.risk, service.state.vix
        thresholds = service.settings.risk.cushion_thresholds
        curve, shape = dash.vix_curve(vix, service.now().date()) if vix else (viz.empty(), None)
        vix_note = vix_advice(vix, service.settings.scanner.strategy)
        nlv = service.nlv_usd() or (service.state.account.net_liquidation if service.state.account else None)   # strikes y posiciones en USD
        mgmt = manage_puts(service.state.positions, nlv, service.now().date(),
                           {t: i.underlying_price for t, i in service.ticker_info.all().items()}, service.settings.management,
                           service.put_management(), service.put_assignments(), service.put_open_dates(), service.put_margins(),
                           service.put_assignment_notes(), service.put_exit_rules(), service.defense_context(), service.state.position_deltas)
        today = service.now().date()
        others = other_positions(service.state.positions, today, nlv,
                                 {t: i.underlying_price for t, i in service.ticker_info.all().items()})
        status = portfolio_status(service.state.account, others, mgmt.puts)
        by_ticker: dict[str, list] = {}
        for s in mgmt.puts:
            by_ticker.setdefault(s.ticker, []).append(s)
        key_of = lambda t: t.replace(" ", "-")                      # IBKR escribe la clase con espacio (BRK B); el histórico, con guion      # noqa: E731
        closes = service.bars.all_closes({key_of(t) for t in by_ticker}) if by_ticker else {}
        pkey = lambda s: f"{s.ticker}|{s.expiry.isoformat()}|{s.strike}|{s.long_strike}"      # noqa: E731  (un gráfico por put o spread)
        mgmt_cfg, weeks_cfg = service.settings.management, service.settings.management.chart_weeks
        minis = {pkey(s): position_mini(closes.get(key_of(s.ticker), []), [s], mgmt_cfg, today, weeks_cfg, s.underlying_price) for s in mgmt.puts}
        pos_charts = [dict(ticker=s.ticker, chart=position_chart(closes.get(key_of(s.ticker), []), [s], mgmt_cfg, today, weeks_cfg, s.underlying_price,
                                                  service.next_earnings(s.ticker)),
                           company=Markup(service.company_card(s.ticker)), plans=[(s, exit_plan(s, mgmt_cfg, today))]) for s in mgmt.puts]
        pc_index = {pkey(s): n for n, s in enumerate(mgmt.puts, 1)}
        charts = dict(
            cushion_current=dash.cushion_meter(risk.current if risk else None, thresholds),
            cushion_look_ahead=dash.cushion_meter(risk.look_ahead if risk else None, thresholds),
            cushion_post=dash.cushion_meter(risk.post_expiration if risk else None, thresholds),
            exposure=dash.exposure_bars(assignment), sectors=dash.sector_bars(sectors), weeks=dash.week_bars(weeks, today),
            vix_history=dash.vix_history(vix) if vix else viz.empty(), vix_curve=curve, vix_shape=shape)
        return render(request, "dashboard.html", no_autorefresh=True, watch_data=True, watch_account=True, status=status, others=others, wmap={w.ticker: w for w in mgmt.weights}, pos_charts=pos_charts, minis=minis, pc_index=pc_index, pkey=pkey, profile_names=[p.name for p in get_profiles()[0]], profile_colors={p.name: p.color for p in get_profiles()[0]}, sectors=sectors, weeks=weeks, assignment=assignment, charts=charts, vix_note=vix_note, mgmt=mgmt, mgmt_cfg=service.settings.management,
                      risk_cfg=thresholds, today_date=today,
                      all_sectors=sorted({s for w in weeks for s in w.amounts} | set(sectors.weights_pct)))

    @app.get("/management/roll", response_class=HTMLResponse)
    async def management_roll(request: Request, key: str = ""):
        """Evalúa en vivo el roll de una put desnuda del Panel (cotiza la cadena); fragmento para la capa del Panel."""
        try:
            ev = await service.roll_evaluation(key)
        except ValueError as exc:
            return render(request, "_roll.html", error=str(exc), ev=None)
        return render(request, "_roll.html", error=None, ev=ev)

    @app.post("/management/profile")
    async def management_profile(request: Request):
        """Cambia el perfil del Scanner (y con él las reglas de gestión) de una put vendida del Panel."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        try:
            service.set_put_profile(str(form.get("key", "")), str(form.get("profile", "")))
        except ValueError:
            return Response("Perfil no válido", status_code=400)
        return RedirectResponse("/#gestion", status_code=303)

    @app.post("/refresh")
    async def refresh():
        """Refresca en segundo plano (no bloquea la petición); la pantalla muestra la actividad."""
        service.launch(service.refresh_all())
        return RedirectResponse("/", status_code=303)

    @app.post("/connection")
    async def connection(mode: str = Form(...), platform: str = Form("")):
        """Cambia de destino: modo de cuenta (real/simulada) y, si se indica, plataforma (TWS/IB Gateway)."""
        if gateway_factory is None:
            return RedirectResponse("/", status_code=303)
        try:
            new_mode = AccountMode(mode)
            new_platform = Platform(platform) if platform else service.settings.ibkr.platform
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Conexión no válida: {platform or mode}")
        service.settings = service.settings.model_copy(
            update={"ibkr": service.settings.ibkr.model_copy(update={"mode": new_mode, "platform": new_platform})}
        )
        await service.switch_gateway(gateway_factory(new_mode))
        return RedirectResponse("/", status_code=303)

    # ---- watchlist -------------------------------------------------------------------------
    @app.get("/watchlist", response_class=HTMLResponse)
    async def watchlist(request: Request, message: str = ""):
        tickers, infos = service.watchlist.list(), service.ticker_info.all()
        return render(request, "watchlist.html", no_autorefresh=True, watch_data=True, tickers=tickers, infos=infos, message=message, groups=message_groups(message),
                      ov=page_charts.watchlist_overview(tickers, infos), modes=service.put_quality())

    # ---- ideas de la comunidad ---------------------------------------------------------------
    routes_tortuga.register(app, service, render, remembered)
    routes_historico.register(app, service, render, remembered)
    routes_universe.register(app, service, render, recall, remembered)

    @app.post("/excluded/dismiss")
    async def excluded_dismiss():
        """Cierra el aviso de tickers sacados de la watchlist (la «x» del aviso)."""
        service.dismiss_excluded_notice()
        return Response(status_code=204)

    # ---- Universo (ficheros .xlsx de RankedStocks y HelloStocks elegidos por el usuario) ------
    def parse_scan(qp, set_name: str = "put") -> dict:
        return scanner_parse.parse_scan(service, qp, set_name)

    def parse_one(qp) -> tuple[str, dict]:
        """(instrumento, resultado de `parse_scan` de sus filtros)."""
        shown, own = split_sets(qp)
        struct = {k: v for k, v in dict(qp).items() if k in STRUCT_KEYS}
        return shown, parse_scan({**struct, **own}, shown)

    def get_profiles() -> tuple[list[Profile], str]:
        """Perfiles guardados desde la aplicación o, mientras no haya, los que salen de los presets y la estrategia."""
        profiles, default = service.stored_profiles() or seed_profiles(service.settings)
        if not any(p.fallback for p in profiles):     # el perfil de las puts que no encajan por DTE siempre existe
            profiles = [*profiles, seed_fallback(service.settings)]
        return profiles, default

    service.profile_provider = get_profiles

    def get_profile_criteria() -> list[tuple]:
        """Filtros grabados AHORA en cada perfil (sin el de las puts que no encajan), para comparar las ideas de la comunidad y el radar de
        Tortuga: (nombre, criterios del juego de put | None, criterios del juego de spread | None)."""
        out = []
        for p in get_profiles()[0]:
            if p.fallback:
                continue
            crit = {inst: parse_scan({"submitted": "1", "mode": inst, **p.set_params(inst)}, inst)["criteria"] if inst in p.instruments else None
                    for inst in ("put", "spread")}
            out.append((p.name, crit["put"], crit["spread"]))
        return out

    service.profile_criteria_provider = get_profile_criteria

    @app.get("/scanner/todos", response_class=HTMLResponse)
    async def scanner_all(request: Request):
        """Un solo escaneo con los filtros de varios perfiles (`pf` repetido; sin ninguno, todos menos el de las puts que no encajan): cada
        put o spread que cumple los de alguno sale una vez, con la columna de los perfiles que cumple. Los filtros son los guardados en cada perfil."""
        profiles, _ = get_profiles()
        candidates = [p for p in profiles if not p.fallback]
        asked = set(request.query_params.getlist("pf"))
        chosen = [p for p in candidates if not asked or p.name in asked]
        sets, errors = [], []
        for p in chosen:
            for inst in p.instruments:
                parsed = parse_scan({"submitted": "1", "mode": inst, **p.set_params(inst)}, inst)
                if parsed["criteria"] is None:
                    errors.append(f"{p.name} ({'put' if inst == 'put' else 'spread'}): {parsed['error']}")
                else:
                    sets.append((p.name, inst, parsed["criteria"]))
        rows, counts = service.scan_profiles(sets)
        return render(request, "scanner_all.html", no_autorefresh=True, rows=rows, counts=counts, candidates=candidates, chosen={p.name for p in chosen}, errors=errors,
                      colors={p.name: p.color for p in profiles}, nlv_usd=service.nlv_usd(), community=service.community_counts(),
                      owned=service.owned_options(), held=service.held_tickers(), techs=service.tech_summaries(ScanOutput([m.short for m in rows], 0, {}, [])),
                      watch_data=True)

    @app.post("/scanner/profile/save")
    async def scanner_profile_save(request: Request):
        """Guarda los filtros actuales del formulario en un perfil (existente o nuevo): el juego del instrumento que se está viendo."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        name = str(form.get("name", "")).strip()
        qp = dict(parse_qsl(str(form.get("query", "")), keep_blank_values=True))
        shown, own = split_sets(qp)
        profiles, default = get_profiles()
        try:
            existing = next((p for p in profiles if p.name.casefold() == name.casefold()), None)
            if existing is not None and existing.mode != shown:      # el perfil es del otro instrumento: se guarda aparte, sin tocar el existente
                name = f"{existing.name} ({shown})"
                existing = next((p for p in profiles if p.name.casefold() == name.casefold()), None)
            if existing is not None:
                saved = existing.with_filters(shown, own)
                profiles = [saved if p is existing else p for p in profiles]
            else:
                saved = Profile(name, (shown,)).with_filters(shown, own)
                profiles = [*profiles, saved]
            service.save_profiles(profiles, default)
        except ValueError:
            return RedirectResponse("/scanner?" + urlencode({**qp, "submitted": "1"}), status_code=303)
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner?" + profile_link(saved), status_code=303)

    @app.post("/scanner/profiles")
    async def scanner_profiles_save(request: Request):
        """Nombre, color, orden, instrumentos activos, gestión, duplicado, borrado y perfil inicial de los perfiles (los filtros se guardan desde el Scanner)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        profiles, default = get_profiles()
        by_name = {p.name: p for p in profiles}
        rows: list[tuple[float, int, Profile]] = []      # (orden elegido, posición, perfil)
        chosen = str(form.get("default", ""))
        new_default = default
        renames: dict[str, str] = {}
        for i in range(int(form.get("n_profiles", 0) or 0)):
            orig = by_name.get(str(form.get(f"pf{i}_orig", "")))
            if orig is None or (form.get(f"pf{i}_del") and not orig.fallback):
                continue
            inst = "spread" if form.get(f"pf{i}_inst") == "spread" else "put" if form.get(f"pf{i}_inst") == "put" else orig.mode
            name = str(form.get(f"pf{i}_name", ""))     # el perfil de las puts que no encajan se renombra pero no se borra
            if orig.fallback and not name.strip():
                name = orig.name
            if name != orig.name:
                renames[orig.name] = name
            color = str(form.get(f"pf{i}_color", orig.color)) if form.get(f"pf{i}_usecolor") else ""
            kept = Profile(name, (inst,), orig.put, orig.spread, orig.management, color, orig.exits, orig.fallback)
            if inst != orig.mode:      # cambia de instrumento: parte de los filtros del anterior
                kept = kept.with_filters(inst, orig.set_params(orig.mode))
            try:
                order = float(str(form.get(f"pf{i}_order", "")).replace(",", "."))
            except ValueError:
                order = float(i + 1)
            rows.append((order, 2 * i, kept))
            if chosen == str(i):
                new_default = kept.name
            if form.get(f"pf{i}_dup"):     # copia justo detrás de la original, con los mismos filtros
                taken = {r[2].name.casefold() for r in rows} | {str(form.get(f"pf{j}_name", "")).casefold() for j in range(int(form.get("n_profiles", 0) or 0))}
                copy_name, n = f"{kept.name} (copia)", 2
                while copy_name.casefold() in taken:
                    copy_name, n = f"{kept.name} (copia {n})", n + 1
                rows.append((order, 2 * i + 1, replace(kept, name=copy_name, fallback=False)))
        out = [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]
        try:
            service.save_profiles(out, new_default)
            service.rename_profile_refs(renames)
        except ValueError as exc:
            return RedirectResponse("/scanner/config?" + urlencode({"error": str(exc)}), status_code=303)
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Perfiles guardados."}), status_code=303)

    # ---- estrategias de salida por perfil ------------------------------------------------------
    @app.get("/exits", response_class=HTMLResponse)
    async def exits_page(request: Request, ok: str = "", error: str = ""):
        cfg = service.settings.management
        profiles, _ = get_profiles()
        open_puts: dict[str, int] = {}
        for name in service.put_assignments().values():
            open_puts[name] = open_puts.get(name, 0) + 1
        cards = []
        for p in profiles:
            rules = p.exit_rules(cfg)
            cards.append(dict(profile=p, rules=rules, base=exits_mod.defaults(p.management, cfg), changed=set(p.exits),
                              text=exits_mod.describe(rules), open_puts=open_puts.get(p.name, 0)))
        style_defaults = {st: {k: getattr(exits_mod.defaults(st, cfg), k) for k in exits_mod.KEYS} for st in exits_mod.STYLES}
        return render(request, "exits.html", cards=cards, fields=exits_mod.FIELDS, ok=ok, error=error, style_defaults=style_defaults,
                      style_text={st: exits_mod.describe(exits_mod.defaults(st, cfg)) for st in exits_mod.STYLES})

    @app.post("/exits/save")
    async def exits_save(request: Request):
        """Guarda la estrategia de salida de cada perfil: solo lo que cambia respecto a su estilo base. Cambiar el estilo (o «Restaurar») vuelve a los
        valores del estilo."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        cfg = service.settings.management
        profiles, default = get_profiles()
        try:
            if int(form.get("n_profiles", -1) or -1) != len(profiles):
                raise ValueError("Los perfiles han cambiado: recarga la página antes de guardar.")
            out = []
            for i, orig in enumerate(profiles):
                style = str(form.get(f"pf{i}_style", orig.management))
                if style not in exits_mod.STYLES:
                    raise ValueError(f"«{orig.name}»: estilo no válido")
                if form.get(f"pf{i}_reset") or style != orig.management:
                    out.append(replace(orig, management=style, exits={}))
                    continue
                current, vals = orig.exit_rules(cfg), {}
                for key, label, kind, nullable, _ in exits_mod.FIELDS:
                    if f"pf{i}_{key}" not in form:
                        continue
                    raw = str(form.get(f"pf{i}_{key}", "")).strip()
                    if not raw:
                        if not nullable:
                            raise ValueError(f"«{orig.name}» · {label}: es obligatorio")
                        vals[key] = None
                    else:
                        vals[key] = _read_num(raw, int if kind == "int" else float, f"«{orig.name}» · {label}")
                if f"pf{i}_itm_action" in form:
                    vals["itm_action"] = str(form.get(f"pf{i}_itm_action"))
                if f"pf{i}_defense" in form:
                    vals["defense"] = str(form.get(f"pf{i}_defense")) == "on"
                try:
                    rules = exits_mod.validate(replace(current, **vals))
                except ValueError as exc:
                    raise ValueError(f"«{orig.name}»: {exc}") from None
                out.append(replace(orig, exits=exits_mod.overrides_of(style, rules, cfg)))
            service.save_profiles(out, default)
        except ValueError as exc:
            return RedirectResponse("/exits?" + urlencode({"error": str(exc)}), status_code=303)
        return RedirectResponse("/exits?" + urlencode({"ok": "Estrategias de salida guardadas."}), status_code=303)

    @app.post("/scanner/profiles/reset")
    async def scanner_profiles_reset():
        service.reset_profiles()
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Perfiles regenerados desde los presets TPC y la estrategia Tastytrade."}), status_code=303)

    # ---- configuraciones y estrategia editables ------------------------------------------------
    @app.get("/scanner/config", response_class=HTMLResponse)
    async def scanner_config_page(request: Request, ok: str = "", error: str = ""):
        sc = service.settings.scanner
        profiles, default_profile = get_profiles()
        vix = [(key, label, "float", getattr(sc.strategy, key)) for key, label, _ in VIX_FIELDS]
        cand = sc.candidates
        gap = []
        if sc.strategy.dte_max > cand.dte_max:
            gap.append(f"el DTE máx. de la estrategia ({sc.strategy.dte_max}) supera el rango guardado ({cand.dte_max})")
        if sc.strategy.strike_below_pct_min < cand.strike_below_pct_min:
            gap.append(f"el descuento mín. de la estrategia ({_fmt(sc.strategy.strike_below_pct_min)} %) es menor que el del rango guardado ({_fmt(cand.strike_below_pct_min)} %)")
        return render(request, "scanner_config.html", profiles=profiles, default_profile=default_profile, fallback=next((p.name for p in profiles if p.fallback), FALLBACK_PROFILE),
                      profiles_saved=service.stored_profiles() is not None, vix=vix, ok=ok, error=error,
                      gap=gap, user_edited=bool(service.meta.get(SCANNER_USER_CONFIG)),
                      ranges={p.name: dte_range(p) for p in profiles})

    @app.post("/scanner/config")
    async def scanner_config_save(request: Request):
        """Aviso del VIX del Panel (lo único de la estrategia que se edita aquí; los filtros viven en los perfiles)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        try:
            strategy = service.settings.scanner.strategy.model_dump()
            for key, label, _ in VIX_FIELDS:
                if key in form:
                    strategy[key] = _read_num(form[key], float, label)
            service.save_scanner_config([p.model_dump() for p in service.settings.scanner.presets], strategy,
                                        service.settings.scanner.spreads.model_dump())
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
            return RedirectResponse(f"/scanner/config?{urlencode({'error': msg})}", status_code=303)
        except ValueError as exc:
            return RedirectResponse(f"/scanner/config?{urlencode({'error': str(exc)})}", status_code=303)
        return RedirectResponse(f"/scanner/config?{urlencode({'ok': 'Guardado el aviso del VIX.'})}", status_code=303)

    @app.post("/scanner/config/reset")
    async def scanner_config_reset():
        service.reset_scanner_config()
        remembered.pop("scanner", None)
        return RedirectResponse(f"/scanner/config?{urlencode({'ok': 'Restaurados los valores de config.yaml.'})}", status_code=303)

    @app.get("/scanner/impact", response_class=HTMLResponse)
    async def scanner_impact(request: Request):
        """Panel «¿Cuánto descarta cada filtro?» (fragmento HTML, se carga al abrir el panel del scanner)."""
        _, parsed = parse_one(request.query_params)
        if parsed["criteria"] is None:
            return render(request, "_impact.html", error=parsed["error"] or "Parámetros no válidos", report=None)
        return render(request, "_impact.html", error=None, report=service.scan_impact(parsed["criteria"]))

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner(request: Request, debug: int = 0):
        if (back := recall(request, "scanner")) is not None:
            return back
        profiles, default_profile = get_profiles()
        if "submitted" not in request.query_params and "reset" not in request.query_params:    # sin consulta: se carga el perfil inicial
            first = next((p for p in profiles if p.name == default_profile), profiles[0])
            return RedirectResponse("/scanner?" + profile_link(first), status_code=303)
        mode, parsed = parse_one(request.query_params)
        out = c = parsed["criteria"]
        if c is not None:
            out = service.scan(c, include_rejections=bool(debug))
        if c is not None and c.technical_active:
            with_history, total = service.history_coverage()
            if with_history < total:
                parsed["warnings"].append(
                    f"Faltan cierres diarios de {total - with_history} de {total} tickers: los filtros de tendencia, soporte, "
                    "medias y días desde el último toque los descartan. El histórico se descarga con la actualización diaria.")
        ref_label = None
        if c is not None:
            ref_label = {
                PriceReference.BID: "bid",
                PriceReference.MID: "mid (media bid/ask)",
                PriceReference.BID_PLUS_SPREAD: f"bid + {c.price_spread_pct:g}% del spread",
            }[c.price_reference]
        if c is not None:
            parsed["warnings"].extend(service.filter_data_gaps(c))
            if (floor_notice := service.put_floor_notice(c if mode == 'put' else None)) is not None:
                parsed["warnings"].append(floor_notice)
            if (stale := service.community_stale()) is not None:
                parsed["warnings"].append(f"Los datos de la comunidad de Tortuga tienen {stale / 24:.0f} días: la rutina diaria no se ha ejecutado "
                                          "(scripts/actualizar_comunidad.ps1).")
            if out is not None and not out.results:   # el aviso cuesta ~1 s: solo cuando no sale nada
                if (hint := explain_empty(service.scan_impact(c))) is not None:
                    parsed["warnings"].append(hint)
        frame_shown = parsed["form"]["ma_frame"]
        if frame_shown not in ("daily", "weekly", "monthly"):    # parámetro no válido: se muestra el de la configuración
            frame_shown = service.criteria().ma_frame
        cand = service.settings.scanner.candidates
        presets = []
        for p in service.settings.scanner.presets:
            qs, on = preset_link(service.settings, p, parsed["form"], request.query_params)
            presets.append(dict(name=p.name, discount=_fmt(p.strike_below_pct_min), dte_min=p.dte_min,
                                dte_max=p.dte_max if p.dte_max is not None else cand.dte_max,
                                min_yield=_fmt(p.min_yield_pct), qs=qs, on=on))
        active_profile = next((p for p in profiles if p.name == request.query_params.get("profile", "") and p.mode == mode), None)   # el de otro instrumento no cuenta
        active_name = active_profile.name if active_profile else ""
        default_qs = profile_link(next((p for p in profiles if p.name == default_profile), profiles[0]))
        profile_buttons = [dict(name=p.name, qs=profile_link(p), on=p.name == active_name, instruments=p.instruments, color=p.color,
                                strategy=p.params().get("use_delta") == "on") for p in profiles]
        # filtros que difieren del perfil guardado (juego visible): se marcan en naranja (cambio pequeño) o rojo
        diff = profile_diff(active_profile.set_params(mode), split_sets(request.query_params)[1]) if active_profile else {}

        def sev(keys: str) -> str:
            worst = {diff[k] for k in keys.split() if k in diff}
            return " chg-bad" if "bad" in worst else " chg-warn" if worst else ""
        # celdas de los resultados que se salen de los límites guardados en el perfil (se marcan en el navegador: ver `scanner.html`)
        marks: dict = {}
        if active_profile and out is not None:
            struct = {k: v for k, v in request.query_params.items() if k in STRUCT_KEYS}
            saved = {n: parse_scan({**struct, **active_profile.set_params(n)}, n)["criteria"] for n in (active_profile.mode,)}
            nlv = service.nlv_usd()
            if mode == "spread":
                rows = [(sp.short, "spread", sp) for sp in out.spreads]
            else:
                rows = [(r, "put", None) for r in out.results]
            for r, kind, sp in rows:
                if saved.get(kind) is not None and (cm := row_marks(r, kind, saved[kind], sp, nlv)):
                    marks[row_key(r, sp)] = cm

        qcfg = service.settings.scanner.quality
        # no depende del perfil (base de config.yaml): se reutiliza entre cargas mientras no cambien los datos
        counts = service._memo("quality_counts", lambda: quality_counts(service.ticker_info.all(), service.watchlist.list(), service.criteria(), qcfg))
        strategy_qs, strategy_on = strategy_link(service.settings, parsed["form"], service.criteria(), request.query_params)
        yield_max = max((r.yield_ref_annualized_pct or 0.0 for r in out.results), default=0.0) if out else 0.0   # escala de las barras del yield
        return render(request, "scanner.html", no_autorefresh=True, out=out, owned=service.owned_options(), held=service.held_tickers(), ref_label=ref_label, watch_data=True, presets=presets, profile_buttons=profile_buttons, active_profile=active_profile, sev=sev, marks=marks, default_qs=default_qs,
                      community=service.community_counts(), techs=service.tech_summaries(out) if out else {},
                      yield_max=yield_max, nlv_usd=service.nlv_usd(), strategy_qs=strategy_qs, strategy_on=strategy_on,
                      count_text=lambda field_name, value: _count_text(counts, (field_name, value)),
                      candidates=service.settings.scanner.candidates,
                      source_names=[s.name for s in service.universe_sources],
                      margin=service.settings.scanner.catalog_margin_pct,
                      sosnoff=dict(dmin=_fmt(service.settings.scanner.spreads.sosnoff_delta_min), dmax=_fmt(service.settings.scanner.spreads.sosnoff_delta_max)),
                      ma_hidden=unavailable_ma_fields(
                          frame_shown, service.settings.trend.history_days, service.settings.scanner.technical.ma_slope_candles),
                      tech_opts=dict(
                          slope_candles=service.settings.scanner.technical.ma_slope_candles,
                          rsi_lookback=service.settings.scanner.technical.rsi_lookback_days,
                          chart_months=service.settings.scanner.technical.chart_months,
                          windows=[(m, f"{m} {'mes' if m == 1 else 'meses'}") for m in service.settings.scanner.technical.trend_windows_months],
                          durations=[(d, _days_label(d)) for d in service.settings.scanner.technical.trend_durations],
                          touches=[(d, _days_label(d)) for d in service.settings.scanner.technical.touch_min_days_options]),
                      quality_opts=dict(
                          liquidity=service.settings.scanner.quality.liquidity_options),
                      report=service.state.last_refresh_report, **parsed)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        """Icono de la pestaña; sin esta ruta el navegador provoca un 404 en el log."""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
               '<rect width="32" height="32" rx="6" fill="#1f6feb"/>'
               '<path d="M6 22l7-8 5 5 8-10" fill="none" stroke="#fff" stroke-width="3" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        return Response(svg, media_type="image/svg+xml")

    @app.get("/chart/strike", response_class=HTMLResponse)
    async def strike_chart(ticker: str, strike: float, long: Optional[float] = None, expiry: Optional[date] = None,
                           prem: Optional[float] = None):
        """Gráfico de cierres mensuales del ticker frente al strike, con los días desde el último toque
        (fragmento HTML que el Scanner muestra en una ventana)."""
        closes = service.bars.closes(ticker)
        if not closes and strike > 0 and await service.ensure_history(ticker):   # fuera de la watchlist: se descarga al abrir el gráfico
            closes = service.bars.closes(ticker)
        if not closes or strike <= 0:
            return HTMLResponse('<p class="muted">Sin histórico de cierres de este ticker: tastytrade no devolvió velas. Se reintenta al abrir el gráfico otra vez.</p>')
        tcfg = service.settings.scanner.technical
        history = strike_history(sorted(closes.items()), strike, service.now().date(), tcfg.chart_months)
        alerts = service.filing_alert_map().get(norm_ticker(ticker), [])
        box = ('<div class="banner warn"><b>⚠ Presentaciones recientes ante la SEC</b><ul style="margin:4px 0 0 18px;padding:0">'
               + "".join(f"<li>{escape(a.text)}</li>" for a in alerts) + "</ul></div>") if alerts else ""
        return HTMLResponse(service.company_card(ticker) + box
                            + strike_chart_html(history, ticker, strike, tcfg.chart_near_pct, service.next_earnings(ticker), long, expiry,
                                                prem, service.now().date()))

    @app.get("/chart/mini.svg")
    async def strike_mini(ticker: str, strike: float, long: Optional[float] = None):
        """Miniatura del gráfico del strike para las filas del Scanner (se pide al hacerse visible la fila)."""
        tcfg = service.settings.scanner.technical
        closes = service.bars.closes(ticker)
        history = strike_history(sorted(closes.items()), strike, service.now().date(), tcfg.chart_months)
        return Response(strike_mini_svg(history, strike, tcfg.chart_near_pct, long), media_type="image/svg+xml",
                        headers={"Cache-Control": "max-age=300"})

    @app.get("/data-version")
    async def data_version():
        """Versión de los datos guardados: el scanner la consulta y se recarga
        si cambia (RF: refresco automático de la tabla al terminar una cotización)."""
        return {"version": service.state.data_version, "account": service.state.account_version, "busy": service.busy,
                "connected": service.state.connected}

    @app.get("/simulate")
    async def simulate_get():
        """La simulación es un POST: un GET (recargar, volver atrás) lleva de nuevo al scanner."""
        return RedirectResponse("/scanner", status_code=303)

    @app.post("/simulate", response_class=HTMLResponse)
    async def simulate(request: Request):
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        selected: list[SelectedContract] = []
        for raw in form.getlist("sel"):
            ticker, expiry, strike, *long_strike = str(raw).split("|")   # un spread añade el strike de la comprada
            qty = int(str(form.get(f"qty_{raw}", "1")) or 1)
            loss = str(form.get(f"ml_{raw}", ""))
            selected.append(SelectedContract(
                ticker, date.fromisoformat(expiry), float(strike), max(1, qty),
                float(long_strike[0]) if long_strike else None, float(loss) if long_strike and loss else None))
        if not selected:
            return render(request, "simulation.html", no_autorefresh=True, result=None, selected=[], error="Selecciona al menos un contrato")
        try:
            result = await service.simulate(selected)
        except ValueError as exc:
            return render(request, "simulation.html", no_autorefresh=True, result=None, selected=selected, error=str(exc))
        return render(request, "simulation.html", no_autorefresh=True, result=result, selected=selected, error=None,
                      sectors=sorted(set(result.before.weights_pct) | set(result.after.weights_pct),
                                     key=lambda s: -result.after.weights_pct.get(s, 0.0)),
                      week_sectors=sorted({s for w in result.weeks_before + result.weeks_after for s in w.amounts}))

    return app
