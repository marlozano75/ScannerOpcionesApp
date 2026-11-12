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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from scanner_opciones.jobs.loop_thread import JobsLoop
from scanner_opciones.ui.loops import LoopSplit
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
from scanner_opciones.portfolio.mgmt_profiles import MgmtProfile
from scanner_opciones.portfolio.management import manage_puts
from scanner_opciones.ui.position_charts import MINI_WEEKS, exit_plan, other_chart, other_mini, position_chart, position_mini, windowed
from scanner_opciones.scanner.engine import ScanOutput
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.tortuga.schedule import FeedSchedule, ScheduleError, describe as describe_schedule, parse_times
from scanner_opciones.scanner.impact import explain_empty
from scanner_opciones.portfolio.classify import dte_range
from scanner_opciones.scanner.profiles import UNASSIGNED, Profile, profile_diff
from scanner_opciones.scanner.profile_marks import row_key, row_marks
from scanner_opciones.scanner.market import vix_advice
from scanner_opciones.scanner.quality import is_exempt, ticker_quality_reject
from scanner_opciones.portfolio import defense as defense_mod, other_mgmt
from scanner_opciones.portfolio.overview import other_positions, portfolio_status
from scanner_opciones.scanner.criteria import commission_model
from scanner_opciones.ui import cds_form, dashboard_charts as dash, page_charts, routes_backtest, routes_criba, routes_cds, routes_comunidad, routes_historico, routes_marketradar, routes_universe, scanner_parse, viz
from scanner_opciones.ui.charts import strike_chart_html, strike_mini_svg

STRIKE_WINDOWS = ((6, "6 meses"), (12, "1 año"), (24, "2 años"))     # ventanas del gráfico del strike; la última, «Todo», usa todo el histórico
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


def div_flag(marks, ticker):
    """◆D junto al ticker con divergencia alcista del MACD(12,26,9) con el mercado en capitulación (`AppService.div_marks`). Solo informa."""
    if not marks or ticker.replace(" ", "-") not in marks:
        return ""
    tip = ("Divergencia alcista del MACD(12,26,9) con el mercado en capitulación: mínimo nuevo de 20 sesiones sin nuevo mínimo del MACD (bajo cero). "
           "Histórico 2004-2026: cierra por encima a 10 sesiones el 72 % (con solo capitulación 61 %, un día normal 56 %), toca +1,5 ATR el 70 %; "
           "383 casos en 108 días. Informativo, no una garantía.")
    return Markup(f' <span class="dv" title="{escape(tip)}">◆D</span>')


TEMPLATES.env.globals["div_flag"] = div_flag
LIGHT_LABEL = {
    TrafficLight.GREEN: "Normal / Holgado",
    TrafficLight.AMBER: "Preocupación",
    TrafficLight.ORANGE: "Riesgo elevado",
    TrafficLight.RED: "Riesgo alto",
    TrafficLight.UNKNOWN: "Sin datos",
}


def _put_key(s) -> str:
    """Clave de una put o spread del Panel (un gráfico por put o spread)."""
    return f"{s.ticker}|{s.expiry.isoformat()}|{s.strike}|{s.long_strike}"


def _pct(v: Optional[float], digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}%"


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:,.0f}"


def _signed(v: Optional[float], digits: int = 0) -> str:
    """Importe con signo y miles: «+1,234» / «−56» (el signo menos tipográfico); «—» sin dato."""
    if v is None:
        return "—"
    if round(v, digits) == 0:        # «−0» (un valor negativo minúsculo) no es negativo: sin signo
        return f"{0:,.{digits}f}"
    return f"{'+' if v > 0 else '−'}{abs(v):,.{digits}f}"


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


from scanner_opciones.ui.common import IDEAS_WAIT_SECONDS, MAX_FORM_FIELDS, NO_DIV_NOTICE, _grp, _load_groups, message_groups, source_initials  # noqa: F401  (los tests y las rutas que quedan)
from scanner_opciones.ui.scanner_forms import (  # noqa: F401  (se vuelven a exportar: los tests importan desde web)
    QUALITY_SELECTS, SEED_COLORS, UNASSIGNED_COLOR, SOLVENCY_CORE, SOLVENCY_UI, SPREAD_FIELDS, STRATEGY_FIELDS,
    STRATEGY_KEYS, STRUCT_KEYS, VIX_FIELDS, _count_text, _fmt, _opt_float,
    _read_num, _required, _sector_of, _threshold_text, parse_scanner_config, parse_spread_config,
    preset_link, profile_href, profile_link, quality_counts, quality_form, read_quality,
    seed_profiles, solvency_controls, split_sets, strategy_link,
)


TEMPLATES.env.globals["viz"] = viz
TEMPLATES.env.filters.update(
    pct=_pct, money=_money, signed=_signed, num=_num, x=_x, light_label=lambda l: LIGHT_LABEL[l], zip=lambda a, b: zip(a, b)
)


class Lazy:
    """Diccionario (o conjunto) que se calcula la primera vez que una plantilla lo toca: el Panel, Comunidad, etc. no usan las columnas de caída y soporte."""
    def __init__(self, build) -> None:
        self._build, self._value = build, None

    def _d(self) -> dict:
        if self._value is None:
            self._value = self._build()
        return self._value

    def __bool__(self) -> bool: return bool(self._d())
    def __len__(self) -> int: return len(self._d())
    def __iter__(self): return iter(self._d())
    def __contains__(self, k) -> bool: return k in self._d()
    def __getitem__(self, k): return self._d()[k]
    def get(self, k, default=None): return self._d().get(k, default)


def create_app(
    service: AppService,
    gateway_factory: Optional[Callable[[AccountMode], BrokerGateway]] = None,
    on_startup: Optional[Callable[[], Awaitable[None]]] = None,
    on_shutdown: Optional[Callable[[], Awaitable[None]]] = None,
    jobs: Optional[JobsLoop] = None,
) -> FastAPI:
    """`jobs`: hilo con su propio bucle de eventos donde corren `on_startup`/`on_shutdown` y todo lo que arrancan (refresco, actualización diaria,
    broker), para que la interfaz no espere a sus tramos síncronos (`ui/loops.py`). Sin él todo va en el bucle del servidor."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if jobs is not None:
            service.jobs_loop = jobs.start()
            service.watchlist.db.split_reader()      # este hilo (el de la interfaz) lee por su propia conexión
        run = jobs.run if jobs is not None else (lambda coro: coro)
        try:
            if on_startup:
                await run(on_startup())
            yield
            if on_shutdown:
                await run(on_shutdown())
        finally:
            if jobs is not None:
                service.jobs_loop = None
                jobs.stop()

    app = FastAPI(title="ScannerOpcionesApp", lifespan=lifespan)
    app.add_middleware(LoopSplit, service=service)             # por dentro de la compresión: ver `ui/loops.py`
    app.add_middleware(GZipMiddleware, minimum_size=1024)      # el HTML del Scanner/Universo pesa MB y se comprime ~10:1

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
            # mapas de todos los tickers: perezosos, porque varias páginas y fragmentos (Backtest, salidas, configuración, impacto) no los tocan
            excluded_notice=service.excluded_notice, sec_alerts=Lazy(service.filing_alert_map), analyst_alerts=Lazy(service.analyst_map), company_names=Lazy(service.company_name_map),
            company_notes=Lazy(service.company_notes_map), div_marks=Lazy(service.div_marks), drops=Lazy(service.drop_map), atr_of=service.atr_of, atr_period=service.settings.scanner.technical.atr_period, drop_cfg=service.settings.scanner.drop, supports=Lazy(service.support_map), sup_cfg=service.settings.scanner.support_levels, use_div="use_div" in request.query_params,
        )
        return TEMPLATES.TemplateResponse(request, name, {**base, **ctx})

    # ---- panel -----------------------------------------------------------------------------
    def panel_positions():
        """Puts vendidas gestionadas y demás posiciones del Panel (con su gestión), comunes a la página y a sus gráficos ampliados."""
        nlv = service.nlv_usd() or (service.state.account.net_liquidation if service.state.account else None)   # strikes y posiciones en USD
        positions = service.state.positions
        mgmt = manage_puts(
            positions, nlv, service.now().date(),
            {t: i.underlying_price for t, i in service.ticker_info.all().items()}, service.settings.management,
            service.put_management(), service.put_assignments(), service.put_open_dates(), service.put_margins(),
            service.put_assignment_notes(), service.put_exit_rules(), service.defense_context(), service.usable_position_deltas(),
            commission_model(service.settings))
        today = service.now().date()
        others = other_positions(positions, today, nlv,
                                 {t: i.underlying_price for t, i in service.ticker_info.all().items()},
                                 service.put_profiles(), service._mgmt_catchall(), service.other_open_dates())
        rules_by_name, leg_deltas, dctx = service.other_exit_rules(), service.usable_leg_deltas(), service.defense_context()
        others = [other_mgmt.assess(x, rules_by_name.get(x.profile), service.settings.management, today, leg_deltas, (dctx.get(x.ticker) or {}).get("iv30"))
                  for x in others]
        return mgmt, others, today

    @app.get("/management/chart", response_class=HTMLResponse)
    async def management_chart(key: str = ""):
        """Gráfico ampliado de una posición del Panel (ficha de la empresa, cierres con ventanas y plan de salida), cargado al pulsar su miniatura."""
        mgmt, others, today = panel_positions()
        mgmt_cfg, weeks_cfg = service.settings.management, service.settings.management.chart_weeks
        key_of = lambda t: t.replace(" ", "-")      # noqa: E731
        for s in mgmt.puts:
            if _put_key(s) == key:
                closes = sorted(service.bars.closes(key_of(s.ticker)).items())
                chart = windowed(lambda w, log: position_chart(closes, [s], mgmt_cfg, today, w, s.underlying_price, service.next_earnings(s.ticker), log), weeks_cfg)
                plan = exit_plan(s, mgmt_cfg, today)
                lines = f"{s.contracts} × {_num(s.strike)}{'/' + _num(s.long_strike) if s.long_strike is not None else ''} · venc. {s.expiry.strftime('%d/%m')} ({s.dte} d) · {plan.text}"
                return HTMLResponse(str(Markup(service.company_card(s.ticker)) + chart + Markup('<ul class="vs" style="margin:6px 0 0;padding-left:16px"><li>') + lines + Markup("</li></ul>")))
        for x in others:
            if x.key == key:
                closes = sorted(service.bars.closes(key_of(x.ticker)).items())
                chart = windowed(lambda w, log: other_chart(closes, x, today, w, x.underlying, service.next_earnings(x.ticker), log), weeks_cfg)
                return HTMLResponse(str(Markup(service.company_card(x.ticker)) + chart))
        return HTMLResponse("La posición ya no está en la cartera.", status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        sectors, weeks = service.diversification()
        assignment, risk, vix = service.assignment(), service.state.risk, service.state.vix
        thresholds = service.settings.risk.cushion_thresholds
        curve, shape = dash.vix_curve(vix, service.now().date()) if vix else (viz.empty(), None)
        vix_note = vix_advice(vix, service.settings.scanner.strategy)
        mgmt, others, today = panel_positions()
        status = portfolio_status(service.state.account, others, mgmt.puts)
        by_ticker: dict[str, list] = {}
        for s in mgmt.puts:
            by_ticker.setdefault(s.ticker, []).append(s)
        key_of = lambda t: t.replace(" ", "-")                      # IBKR escribe la clase con espacio (BRK B); el histórico, con guion      # noqa: E731
        closes = service.bars.all_closes({key_of(t) for t in by_ticker} | {key_of(x.ticker) for x in others}) if (by_ticker or others) else {}
        mgmt_cfg = service.settings.management
        minis = {_put_key(s): position_mini(closes.get(key_of(s.ticker), []), [s], mgmt_cfg, today, MINI_WEEKS, s.underlying_price) for s in mgmt.puts}
        minis.update({x.key: other_mini(closes.get(key_of(x.ticker), []), x, today, MINI_WEEKS, x.underlying) for x in others})
        charts = dict(
            cushion_current=dash.cushion_meter(risk.current if risk else None, thresholds),
            cushion_look_ahead=dash.cushion_meter(risk.look_ahead if risk else None, thresholds),
            exposure=dash.exposure_bars(assignment), sectors=dash.sector_bars(sectors), weeks=dash.week_bars(weeks, today),
            vix_history=dash.vix_history(vix) if vix else viz.empty(), vix_curve=curve, vix_shape=shape)
        return render(request, "dashboard.html", no_autorefresh=True, watch_data=True, watch_account=True, status=status, usd_per_base=service.usd_per_base(), others=others, zone_short={**defense_mod.ZONE_SHORT, **other_mgmt.LONG_SHORT}, attention=mgmt.attention + sum(1 for x in others for a in x.alerts if a.level == "warn"), wmap={w.ticker: w for w in mgmt.weights}, minis=minis, pkey=_put_key, profile_names=service.all_profile_names(), profile_colors={**{p.name: p.color for p in get_profiles()[0]}, **{p.name: (p.color or (UNASSIGNED_COLOR if p.catchall else "")) for p in service.management_profiles()}}, trader_profiles=service.put_trader_profiles(), sectors=sectors, weeks=weeks, assignment=assignment, charts=charts, vix_note=vix_note, cap=service.capitulation(), mgmt=mgmt, mgmt_cfg=service.settings.management,
                      risk_cfg=thresholds, today_date=today,
                      all_sectors=sorted({s for w in weeks for s in w.amounts} | set(sectors.weights_pct)))

    @app.get("/management/roll", response_class=HTMLResponse)
    async def management_roll(request: Request, key: str = ""):
        """Evalúa en vivo el roll de una put desnuda del Panel (cotiza la cadena); fragmento para la capa del Panel."""
        try:
            ev = await service.on_jobs(service.roll_evaluation(key))      # cotiza con el broker: en el bucle de los jobs
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
                      ov=page_charts.watchlist_overview(tickers, infos), modes=service.put_quality(), tmodes=service.ticker_modes())

    # ---- ideas de la comunidad ---------------------------------------------------------------
    routes_comunidad.register(app, service, render, remembered)
    routes_marketradar.register(app, service, render)
    routes_historico.register(app, service, render, remembered)
    routes_universe.register(app, service, render, recall, remembered)
    routes_criba.register(app, service, render)
    routes_backtest.register(app, service, render)

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
        return service.stored_profiles() or seed_profiles(service.settings)

    service.profile_provider = get_profiles
    routes_cds.register(app, service, get_profiles)

    def get_profile_criteria() -> list[tuple]:
        """Filtros grabados AHORA en cada perfil, para comparar las ideas de la comunidad y el radar de
        Tortuga: (nombre, criterios del juego de put | None, criterios del juego de spread | None)."""
        out = []
        for p in get_profiles()[0]:
            if p.mode == "cds":      # el Call Debit Spread no se compara con puts ni spreads de puts
                continue
            crit = {inst: parse_scan({"submitted": "1", "mode": inst, "profile": p.name, **p.set_params(inst)}, inst)["criteria"] if inst in p.instruments else None
                    for inst in ("put", "spread")}
            out.append((p.name, crit["put"], crit["spread"]))
        return out

    service.profile_criteria_provider = get_profile_criteria

    @app.get("/scanner/todos", response_class=HTMLResponse)
    async def scanner_all(request: Request):
        """Un solo escaneo con los filtros de varios perfiles (`pf` repetido; sin ninguno, todos): cada
        put o spread que cumple los de alguno sale una vez, con la columna de los perfiles que cumple. Los filtros son los guardados en cada perfil."""
        profiles, _ = get_profiles()
        candidates = list(profiles)
        asked = set(request.query_params.getlist("pf"))
        chosen = [p for p in candidates if not asked or p.name in asked]
        sets, errors = [], []
        counts_cds: dict[str, dict[str, int]] = {}
        cds_hits: dict = {}      # (ticker, vencimiento, strike comprado, strike vendido) → (resultado, [perfiles]); tabla aparte: otras columnas que las puts
        for p in chosen:
            for inst in p.instruments:
                if inst == "cds":      # el CDS no comparte columnas con las puts: va en su propia tabla, bajo la de puts y spreads
                    parsed = cds_form.parse_cds(service, {"submitted": "1", "mode": "cds", "profile": p.name, **cds_form.with_allw(p.set_params("cds"))})
                    if parsed["cds"] is None:
                        errors.append(f"{p.name} (CDS): {parsed['error']}")
                        continue
                    found = service.scan_cds(parsed["cds"], **parsed["ref"])
                    for r in found:
                        c = r.long.contract
                        cds_hits.setdefault((c.ticker, c.expiry, c.strike, r.short.contract.strike), (r, []))[1].append(p.name)
                    counts_cds[p.name] = {"cds": len(found)}
                    continue
                parsed = parse_scan({"submitted": "1", "mode": inst, "profile": p.name, **p.set_params(inst)}, inst)
                if parsed["criteria"] is None:
                    errors.append(f"{p.name} ({'put' if inst == 'put' else 'spread'}): {parsed['error']}")
                else:
                    sets.append((p.name, inst, parsed["criteria"]))
        rows, counts = service.scan_profiles(sets)
        for name, c in counts_cds.items():
            counts.setdefault(name, {}).update(c)
        infos, today = service.infos(), service.now().date()
        ordered = sorted(cds_hits.values(), key=lambda h: -h[0].score)
        cds_rows = [routes_cds.CdsRow(r, infos.get(r.long.contract.ticker), (r.long.contract.expiry - today).days) for r, _ in ordered]
        cds_names = [names for _, names in ordered]      # perfiles que cumple cada fila, en el mismo orden
        return render(request, "scanner_all.html", no_autorefresh=True, rows=rows, counts=counts, cds_rows=cds_rows, cds_names=cds_names, candidates=candidates, chosen={p.name for p in chosen}, errors=errors,
                      colors={p.name: p.color for p in profiles}, nlv_usd=service.nlv_usd(),
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
        return RedirectResponse(profile_href(saved), status_code=303)

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
            if orig is None or form.get(f"pf{i}_del"):
                continue
            inst = str(form.get(f"pf{i}_inst")) if form.get(f"pf{i}_inst") in ("put", "spread", "cds") else orig.mode
            name = str(form.get(f"pf{i}_name", ""))
            if name != orig.name:
                renames[orig.name] = name
            color = str(form.get(f"pf{i}_color", orig.color)) if form.get(f"pf{i}_usecolor") else ""
            kept = Profile(name, (inst,), orig.put, orig.spread, orig.cds, orig.management, color, orig.exits)
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
                rows.append((order, 2 * i + 1, replace(kept, name=copy_name)))
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
        mgmt_profiles = service.management_profiles()
        open_puts: dict[str, int] = {}
        for name in service.put_assignments().values():
            open_puts[name] = open_puts.get(name, 0) + 1
        def card(p, prefix: str, tag: str, active: set) -> dict:
            rules = p.exit_rules(cfg)
            base = exits_mod.defaults(p.management, cfg)
            return dict(profile=p, prefix=prefix, tag=tag, rules=rules, sections=exits_mod.sections(active), base=base, changed=set(p.exits),
                        diff=exits_mod.diff_rules(base, rules), style_label=exits_mod.style_label(p.management),
                        text=exits_mod.describe(rules), open_puts=open_puts.get(p.name, 0))
        # un perfil de escaneo gestiona las estrategias de sus instrumentos; uno de gestión, todas
        cards = [card(p, f"pf{i}", p.mode, {"debit" if i_ == "cds" else i_ for i_ in p.instruments}) for i, p in enumerate(profiles)]
        all_strategies = {k for k, _ in exits_mod.STRATEGIES}
        mgmt_cards = [card(p, f"mf{i}", "gestión", all_strategies) for i, p in enumerate(mgmt_profiles)]
        styles = exits_mod.all_styles()
        style_defaults = {st: {k: getattr(exits_mod.defaults(st, cfg), k) for k in exits_mod.KEYS} for st in styles}
        used: dict[str, list[str]] = {}
        for p in [*profiles, *mgmt_profiles]:
            used.setdefault(p.management, []).append(p.name)
        style_cards = [dict(name=st, label=exits_mod.style_label(st), custom=st in exits_mod.custom_styles(), parent=exits_mod.style_label(exits_mod.root_style(st)),
                            sm=exits_mod.style_summary(exits_mod.defaults(st, cfg)), text=exits_mod.describe(exits_mod.defaults(st, cfg)),
                            changed=sorted(exits_mod.custom_styles().get(st, ("", {}))[1]), used=used.get(st, []))
                       for st in styles]
        return render(request, "exits.html", cards=cards, styles=styles, style_labels={st: exits_mod.style_label(st) for st in styles}, style_cards=style_cards, mgmt_cards=mgmt_cards, fields={k: (lab, kind, nul, hlp) for k, lab, kind, nul, hlp in exits_mod.FIELDS}, extra=exits_mod.EXTRA_FIELDS, dcfg=cfg.defense, alerts=exits_mod.ALERTS, ok=ok, error=error, style_defaults=style_defaults,
                      style_text={st: exits_mod.describe(exits_mod.defaults(st, cfg)) for st in styles})

    @app.post("/exits/save")
    async def exits_save(request: Request):
        """Guarda la estrategia de salida de cada perfil: solo lo que cambia respecto a su estilo base. Cambiar el estilo (o «Restaurar») vuelve a los
        valores del estilo."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        cfg = service.settings.management
        profiles, default = get_profiles()
        mgmt_profiles = service.management_profiles()

        pending: dict[str, tuple[str, dict]] = {}     # estilos base nuevos de este envío: se guardan solo si todos los perfiles son válidos

        def apply_card(orig, prefix: str):
            """Devuelve una copia de `orig` (perfil de escaneo o de gestión) con las salidas del formulario; lanza ValueError con el nombre del perfil."""
            style = str(form.get(f"{prefix}_style", orig.management))
            if not exits_mod.is_style(style):
                raise ValueError(f"«{orig.name}»: estilo no válido")
            new_style = str(form.get(f"{prefix}_newstyle", "")).strip()
            if not new_style and (form.get(f"{prefix}_reset") or style != orig.management):
                return replace(orig, management=style, exits={})
            current, vals = (orig.exit_rules(cfg) if style == orig.management else exits_mod.defaults(style, cfg)), {}
            for key, label, kind, nullable, _ in exits_mod.FIELDS:
                if f"{prefix}_{key}" not in form:
                    continue
                raw = str(form.get(f"{prefix}_{key}", "")).strip()
                if not raw:
                    if not nullable:
                        raise ValueError(f"«{orig.name}» · {label}: es obligatorio")
                    vals[key] = None
                else:
                    vals[key] = _read_num(raw, int if kind == "int" else float, f"«{orig.name}» · {label}")
            if f"{prefix}_itm_action" in form:
                vals["itm_action"] = str(form.get(f"{prefix}_itm_action"))
            if f"{prefix}_defense" in form:
                vals["defense"] = str(form.get(f"{prefix}_defense")) == "on"
            try:
                rules = exits_mod.validate(replace(current, **vals))
                if new_style:      # «Guardar como estilo base»: lo que hay en la tarjeta pasa a ser un estilo y el perfil lo usa sin cambios propios
                    root = exits_mod.root_style(style)
                    name = exits_mod.validate_style_name(new_style)
                    pending[name] = (root, exits_mod.overrides_of(root, rules, cfg))
                    return replace(orig, management=name, exits={})
            except ValueError as exc:
                raise ValueError(f"«{orig.name}»: {exc}") from None
            return replace(orig, exits=exits_mod.overrides_of(style, rules, cfg))

        try:
            if int(form.get("n_profiles", -1) or -1) != len(profiles) or int(form.get("n_mgmt", -1) or -1) != len(mgmt_profiles):
                raise ValueError("Los perfiles han cambiado: recarga la página antes de guardar.")
            new_scan = [apply_card(p, f"pf{i}") for i, p in enumerate(profiles)]
            new_mgmt = [apply_card(p, f"mf{i}") for i, p in enumerate(mgmt_profiles)]
            for name, (root, ov) in pending.items():       # los estilos antes que los perfiles que los usan
                service.save_exit_style(name, root, ov)
            service.save_profiles(new_scan, default)
            service.save_management_profiles(new_mgmt)
        except ValueError as exc:
            return RedirectResponse("/exits?" + urlencode({"error": str(exc)}), status_code=303)
        msg = "Estrategias de salida guardadas." + (f" Estilo base guardado: {', '.join(pending)}." if pending else "")
        return RedirectResponse("/exits?" + urlencode({"ok": msg}), status_code=303)

    @app.post("/exits/styles/delete")
    async def exits_style_delete(request: Request):
        """Borra un estilo base propio (solo si ningún perfil lo usa)."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        try:
            service.delete_exit_style(str(form.get("name", "")))
        except ValueError as exc:
            return RedirectResponse("/exits?" + urlencode({"error": str(exc)}), status_code=303)
        return RedirectResponse("/exits?" + urlencode({"ok": "Estilo base borrado."}), status_code=303)

    @app.post("/scanner/profiles/reset")
    async def scanner_profiles_reset():
        service.reset_profiles()
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Perfiles regenerados desde los presets TPC y la estrategia Tastytrade."}), status_code=303)

    # ---- configuraciones y estrategia editables ------------------------------------------------
    def config_page(request: Request, ok: str = "", error: str = ""):
        """Página de /scanner/config."""
        sc = service.settings.scanner
        profiles, default_profile = get_profiles()
        vix = [(key, label, "float", getattr(sc.strategy, key)) for key, label, _ in VIX_FIELDS]
        cand = sc.candidates
        gap = []
        if sc.strategy.dte_max > cand.dte_max:
            gap.append(f"el DTE máx. de la estrategia ({sc.strategy.dte_max}) supera el rango guardado ({cand.dte_max})")
        if sc.strategy.strike_below_pct_min < cand.strike_below_pct_min:
            gap.append(f"el descuento mín. de la estrategia ({_fmt(sc.strategy.strike_below_pct_min)} %) es menor que el del rango guardado ({_fmt(cand.strike_below_pct_min)} %)")
        return render(request, "scanner_config.html", profiles=profiles, default_profile=default_profile, unassigned=UNASSIGNED,
                      profiles_saved=service.stored_profiles() is not None, vix=vix, ok=ok, error=error,
                      gap=gap, user_edited=bool(service.meta.get(SCANNER_USER_CONFIG)), feeds=service.contract_feeds_info(),
                      ranges={p.name: dte_range(p) for p in profiles},
                      mgmt_profiles=service.management_profiles(), catchall=service._mgmt_catchall(),
                      styles=exits_mod.all_styles(), style_labels={st: exits_mod.style_label(st) for st in exits_mod.all_styles()})

    @app.get("/scanner/config", response_class=HTMLResponse)
    async def scanner_config_page(request: Request, ok: str = "", error: str = ""):
        return config_page(request, ok, error)

    @app.post("/scanner/profiles/mgmt")
    async def scanner_mgmt_profiles_save(request: Request):
        """Perfiles de gestión: nombre, color y borrado (el catch-all no se renombra ni se borra), más una fila para crear uno nuevo. Las salidas se editan en /exits."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        orig = service.management_profiles()
        try:
            if int(form.get("n_mgmt", -1) or -1) != len(orig):
                raise ValueError("Los perfiles han cambiado: recarga la página antes de guardar.")
            out, renames = [], {}
            for i, p in enumerate(orig):
                if form.get(f"mf{i}_del") and not p.catchall:
                    continue
                name = p.name if p.catchall else (str(form.get(f"mf{i}_name", p.name)).strip() or p.name)
                color = str(form.get(f"mf{i}_color", "")) if form.get(f"mf{i}_usecolor") else ""
                if name != p.name:
                    renames[p.name] = name
                out.append(replace(p, name=name, color=color))
            new_name = str(form.get("new_name", "")).strip()
            if new_name:
                color = str(form.get("new_color", "")) if form.get("new_usecolor") else ""
                style = str(form.get("new_style", "tpc"))
                out.append(MgmtProfile(new_name, style if exits_mod.is_style(style) else "tpc", color, {}, "", False))
            service.save_management_profiles(out, renames)
        except ValueError as exc:
            return RedirectResponse("/scanner/config?" + urlencode({"error": str(exc)}), status_code=303)
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Perfiles de gestión guardados."}), status_code=303)

    @app.post("/scanner/feeds")
    async def scanner_feeds_save(request: Request):
        """Fuentes de contratos: qué fuentes publican sus tickers como fuente del Universo."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        names = [str(n) for n in form.getlist("feed")]
        service.set_feed_publish({n: form.get(f"publish_{n}") is not None for n in names})
        await service.sync_feed_sources()
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Fuentes de contratos guardadas."}), status_code=303)

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
        if request.query_params.get("mode") == "cds":
            report, error = routes_cds.impact(service, get_profiles()[0], request.query_params)
            return render(request, "_impact.html", error=error, report=report, unit="spreads")
        _, parsed = parse_one(request.query_params)
        if parsed["criteria"] is None:
            return render(request, "_impact.html", error=parsed["error"] or "Parámetros no válidos", report=None, unit="contratos")
        return render(request, "_impact.html", error=None, report=service.scan_impact(parsed["criteria"]), unit="contratos")

    @app.get("/scanner/cds-check", response_class=HTMLResponse)
    async def scanner_cds_check(request: Request):
        """Consulta de un CDS concreto (ticker, vencimiento y strikes): sus parámetros y los filtros del formulario que no cumple (fragmento HTML)."""
        found, error = routes_cds.check(service, get_profiles()[0], request.query_params)
        company = service.company_name_map()
        return render(request, "_cds_check.html", error=error, ck=found, nlv_usd=service.nlv_usd(), today=service.now().date(),
                      info=service.infos().get(found.ticker) if found else None, company=company.get(found.ticker, "") if found else "")

    def profile_buttons_for(profiles: list[Profile], active_name: str) -> list[dict]:
        """Botones de la barra de perfiles del Scanner (los tres instrumentos)."""
        return [dict(name=p.name, qs=profile_link(p), href=profile_href(p), mode=p.mode, on=p.name == active_name, instruments=p.instruments, color=p.color,
                     strategy=p.params().get("use_delta") == "on") for p in profiles]

    def default_profile_qs(profiles: list[Profile], default_profile: str) -> str:
        scan_profiles = [p for p in profiles if p.mode != "cds"] or profiles
        return profile_link(next((p for p in scan_profiles if p.name == default_profile), scan_profiles[0]))

    def sev_of(diff: dict, keys: str) -> str:
        """Clase CSS de un chip según lo tocado respecto al perfil guardado (`diff`): naranja = cambio pequeño, rojo = grande."""
        worst = {diff[k] for k in keys.split() if k in diff}
        return " chg-bad" if "bad" in worst else " chg-warn" if worst else ""

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner(request: Request, debug: int = 0):
        if (back := recall(request, "scanner")) is not None:
            return back
        profiles, default_profile = get_profiles()
        if request.query_params.get("mode") == "cds":      # Call Debit Spread (perfil MTO CDS): sus propios filtros y columnas en la misma pantalla
            cds_ctx = routes_cds.page(service, profiles, request.query_params)
            active_cds, cds_diff = cds_ctx["active"], cds_ctx.pop("diff")
            return render(request, "scanner.html", no_autorefresh=True, watch_data=True, owned=service.owned_options(), held=service.held_tickers(),
                          profile_buttons=profile_buttons_for(profiles, active_cds.name if active_cds else ""), active_profile=active_cds,
                          sev=lambda keys: sev_of(cds_diff, keys), marks={}, nlv_usd=service.nlv_usd(), techs={},
                          default_qs=default_profile_qs(profiles, default_profile), report=service.state.last_refresh_report,
                          criteria=None, out=None, **cds_ctx)
        if "submitted" not in request.query_params and "reset" not in request.query_params:    # sin consulta: se carga el perfil inicial
            first = next((p for p in profiles if p.name == default_profile), profiles[0])
            return RedirectResponse(profile_href(first), status_code=303)
        mode, parsed = parse_one(request.query_params)
        out = c = parsed["criteria"]
        if c is not None:
            out = service.scan(c, include_rejections=bool(debug))
            if "use_div" in request.query_params:
                out = service.keep_div(out)
                if not service.div_marks():
                    parsed["warnings"].append(NO_DIV_NOTICE)
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
        default_qs = default_profile_qs(profiles, default_profile)
        profile_buttons = profile_buttons_for(profiles, active_name)
        # filtros que difieren del perfil guardado (juego visible): se marcan en naranja (cambio pequeño) o rojo
        diff = profile_diff(active_profile.set_params(mode), split_sets(request.query_params)[1]) if active_profile else {}

        def sev(keys: str) -> str:
            return sev_of(diff, keys)
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
                      techs=service.tech_summaries(out) if out else {},
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
                           prem: Optional[float] = None, kind: str = "",
                           exit: Optional[float] = None, need: Optional[float] = None):
        """Gráfico de cierres mensuales del ticker frente al strike, con los días desde el último toque
        (fragmento HTML que el Scanner muestra en una ventana)."""
        closes = service.bars.closes(ticker)
        if not closes and strike > 0 and await service.on_jobs(service.ensure_history(ticker)):   # fuera de la watchlist: se descarga al abrir el gráfico
            closes = service.bars.closes(ticker)
        if not closes or strike <= 0:
            return HTMLResponse('<p class="muted">Sin histórico de cierres de este ticker: tastytrade no devolvió velas. Se reintenta al abrir el gráfico otra vez.</p>')
        tcfg = service.settings.scanner.technical
        today = service.now().date()
        bars = sorted(closes.items())
        total = max((today - bars[0][0]).days // 30 + 1, 2)          # meses de histórico disponibles («Todo»)
        wins = [(m, label) for m, label in STRIKE_WINDOWS if m < total] + [(total, "Todo")]
        default = next((i for i, (m, _) in enumerate(wins) if m == tcfg.chart_months), len(wins) - 1)
        earnings = service.next_earnings(ticker)
        buttons = "".join(f'<button type="button" class="shw-btn{" on" if i == default else ""}" data-m="{m}">{label}</button>' for i, (m, label) in enumerate(wins))
        views = "".join(
            f'<div class="shw-view" data-m="{m}" data-log="{int(lg)}"{"" if (i == default and not lg) else " hidden"}>'
            + strike_chart_html(strike_history(bars, strike, today, m), ticker, strike, tcfg.chart_near_pct, earnings, long, expiry, prem, today, lg, call=kind == "cds",
                                  exit_price=exit if kind == "cds" else None, need_pct=need if kind == "cds" else None)
            + "</div>" for i, (m, _) in enumerate(wins) for lg in (False, True))
        alerts = service.filing_alert_map().get(norm_ticker(ticker), [])
        box = ('<div class="banner warn"><b>⚠ Presentaciones recientes ante la SEC</b><ul style="margin:4px 0 0 18px;padding:0">'
               + "".join(f"<li>{escape(a.text)}</li>" for a in alerts) + "</ul></div>") if alerts else ""
        bar = (f'<div class="shw-bar">{buttons}<button type="button" class="shw-log" aria-pressed="false" '
               f'title="Escala logarítmica del eje de precios">Log</button></div>')
        return HTMLResponse(service.company_card(ticker) + box + f'<div class="shw">{bar}{views}</div>')

    @app.get("/chart/mini.svg")
    async def strike_mini(ticker: str, strike: float, long: Optional[float] = None, kind: str = ""):
        """Miniatura del gráfico del strike para las filas del Scanner (se pide al hacerse visible la fila)."""
        tcfg = service.settings.scanner.technical
        closes = service.bars.closes(ticker)
        history = strike_history(sorted(closes.items()), strike, service.now().date(), tcfg.chart_months)
        return Response(strike_mini_svg(history, strike, tcfg.chart_near_pct, long, call=kind == "cds"), media_type="image/svg+xml",
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
