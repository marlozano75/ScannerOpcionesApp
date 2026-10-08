"""Interfaz web local (FastAPI + Jinja2). Sin lógica de negocio: solo llama al AppService."""
from __future__ import annotations

import logging
import tempfile
from urllib.parse import urlencode
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from scanner_opciones.app.service import AppService, SelectedContract
from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.domain.enums import AccountMode, PriceReference, TrafficLight
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES
from scanner_opciones.scanner.quality import is_exempt, ticker_quality_reject
from scanner_opciones.universe.sources import ALL, load_sources, merge
from scanner_opciones.watchlist.parser import parse_text, parse_tokens

log = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
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


def _x(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.2f}x"


def _num(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _mcap_label(m: float) -> str:
    """Capitalización mínima en millones de dólares, legible: 2000 -> «2 B$» (miles de millones)."""
    return f"{m / 1000:g} B$" if m >= 1000 else f"{m:g} M$"


def _days_label(days: int) -> str:
    """7 -> «1 semana», 30 -> «1 mes», 365 -> «1 año»; otros valores, «N días»."""
    for size, one, many in ((365, "año", "años"), (30, "mes", "meses"), (7, "semana", "semanas")):
        if days % size == 0:
            n = days // size
            return f"{n} {one if n == 1 else many}"
    return f"{days} días"


# Filtros de calidad de la empresa: (campo del formulario, campo del criterio, tipo, lista de opciones, etiqueta)
QUALITY_SELECTS = (
    ("q_quarters", "min_positive_quarters", int, "positive_quarters_options", "Trimestres con beneficios"),
    ("q_mcap", "min_market_cap_m", float, "market_cap_options_m", "Capitalización mínima"),
    ("q_liq", "min_option_liquidity", int, "liquidity_options", "Liquidez de opciones"),
    ("q_lev", "max_liabilities_to_equity", float, "leverage_options", "Apalancamiento máximo"),
)


# Filtros de solvencia / calidad del flujo de caja del Universo, con grado de exigencia (flexible, estándar, estricto):
# (parámetro, campo del criterio, métrica en scanner.quality.thresholds, etiqueta, signo, unidad)
SOLVENCY_UI = (
    ("q_de", "max_debt_to_equity", "debt_to_equity", "Deuda / patrimonio", "≤", ""),
    ("q_cov", "min_interest_coverage", "interest_coverage", "Cobertura de intereses", "≥", "×"),
    ("q_cash", "min_cash_to_short_debt", "cash_to_short_debt", "Efectivo / deuda a corto plazo", "≥", "×"),
    ("q_ocfd", "min_ocf_to_debt", "ocf_to_debt", "Flujo operativo / deuda", "≥", "%"),
    ("q_capex", "max_capex_to_ocf", "capex_to_ocf", "CapEx / flujo operativo", "≤", "%"),
    ("q_fcfa", "min_fcf_to_assets", "fcf_to_assets", "FCF / activos", "≥", "%"),
    ("q_bb", "min_net_buyback_pct", "net_buyback_pct", "Recompra neta de acciones", "≥", "pct"),
)
SOLVENCY_CORE = ("q_de", "q_cov", "q_cash", "q_ocfd")   # el bloque «Solvencia»; los otros tres son indicadores opcionales


def _threshold_text(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value * 100:g} %"
    if unit == "pct":
        return f"{value:g} %"
    return f"{value:g}{unit}"


def solvency_controls(qcfg, form: dict) -> list[dict]:
    """Selectores de solvencia para la plantilla: cada opción lleva su umbral («Estándar (≤ 1)»)."""
    out = []
    for key, _, metric, label, sign, unit in SOLVENCY_UI:
        thresholds = getattr(qcfg.thresholds, metric)
        out.append(dict(
            key=key, label=label, core=key in SOLVENCY_CORE, value=form.get(key, ""),
            options=[(level, f"{text} ({sign} {_threshold_text(thresholds[level], unit)})") for level, text in qcfg.level_labels.items()],
        ))
    return out


def quality_form(base) -> dict:
    """Valores del formulario de calidad a partir de los criterios (sin filtros por defecto)."""
    return {
        "q_profit": base.require_profitable, "q_earn": base.avoid_earnings, "q_fcf": base.require_positive_fcf,
        "q_quarters": "" if base.min_positive_quarters is None else str(base.min_positive_quarters),
        "q_mcap": "" if base.min_market_cap_m is None else _fmt(base.min_market_cap_m),
        "q_liq": "" if base.min_option_liquidity is None else str(base.min_option_liquidity),
        "q_lev": "" if base.max_liabilities_to_equity is None else _fmt(base.max_liabilities_to_equity),
        **{key: "" for key, *_ in SOLVENCY_UI},
    }


def read_quality(qp, form: dict, overrides: dict, qcfg, earnings: bool, liquidity: bool = True, solvency: bool = False) -> None:
    """Lee los filtros de calidad de la URL (scanner y Universo comparten parser). Cada valor se valida contra las
    listas permitidas de la configuración. `earnings`: el filtro de resultados es por contrato, solo existe en el scanner.
    `liquidity`: la liquidez de las opciones solo se filtra en el scanner (en el Universo es solo un indicador).
    `solvency`: los filtros de solvencia con grado de exigencia solo existen en el Universo."""
    form["q_profit"], form["q_fcf"] = "q_profit" in qp, "q_fcf" in qp
    overrides["require_profitable"], overrides["require_positive_fcf"] = form["q_profit"], form["q_fcf"]
    if earnings:
        form["q_earn"] = "q_earn" in qp
        overrides["avoid_earnings"] = form["q_earn"]
    for key, field_name, cast, options, label in QUALITY_SELECTS:
        if key == "q_liq" and not liquidity:
            continue
        form[key] = qp.get(key, "").strip()
        overrides[field_name] = _required(form[key], cast, label) if form[key] else None
        if overrides[field_name] is not None and overrides[field_name] not in getattr(qcfg, options):
            raise ValueError(f"{label} no permitida")
    if solvency:
        for key, field_name, metric, label, _, _ in SOLVENCY_UI:
            level = qp.get(key, "").strip()
            if level and level not in qcfg.level_labels:
                raise ValueError(f"{label}: grado de exigencia no permitido")
            form[key] = level
            overrides[field_name] = getattr(qcfg.thresholds, metric)[level] if level else None


def _sector_of(row, sector_col: Optional[int], watchlist_sectors: dict) -> Optional[str]:
    """Sector de una fila del Universo: el de IBKR si el ticker está en la watchlist y, si no, el del fichero."""
    if watchlist_sectors.get(row.ticker):
        return watchlist_sectors[row.ticker]
    if sector_col is not None:
        text = row.cells[sector_col].text
        return None if text in ("", "—") else text
    return None


def _fmt(v) -> str:
    """Número sin ceros sobrantes para los campos del formulario (20.0 -> '20')."""
    return f"{v:g}"


def _required(raw: str, cast, label: str):
    if not raw:
        raise ValueError(f"indica un valor para {label}")
    try:
        return cast(raw.replace(",", "."))
    except ValueError:
        raise ValueError(f"{label}: '{raw}' no es un número válido") from None


def _opt_float(text: str) -> Optional[float]:
    text = (text or "").strip().replace(",", ".")
    return float(text) if text else None


TEMPLATES.env.filters.update(
    pct=_pct, money=_money, num=_num, x=_x, light_label=lambda l: LIGHT_LABEL[l], zip=lambda a, b: zip(a, b)
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
            state=service.state, mode=service.settings.ibkr.mode.value, busy=service.busy,
            refresh_minutes=service.settings.refresh_interval_minutes,
            delay_minutes=service.settings.ibkr.delay_minutes,
            pacing_wait=service.pacing_wait_seconds() if service.busy else 0,
            market_closed=service.settings.market.pause_when_closed and not service.market_open(),
            next_open=service.market.next_open(service.now()), path=request.url.path,
        )
        return TEMPLATES.TemplateResponse(request, name, {**base, **ctx})

    # ---- panel -----------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        sectors, weeks = service.diversification()
        return render(request, "dashboard.html", sectors=sectors, weeks=weeks, assignment=service.assignment(),
                      all_sectors=sorted({s for w in weeks for s in w.amounts} | set(sectors.weights_pct)))

    @app.post("/refresh")
    async def refresh():
        """Refresca en segundo plano (no bloquea la petición); la pantalla muestra la actividad."""
        service.launch(service.refresh_all())
        return RedirectResponse("/", status_code=303)

    @app.post("/connection")
    async def connection(mode: str = Form(...)):
        if gateway_factory is None:
            return RedirectResponse("/", status_code=303)
        try:
            new_mode = AccountMode(mode)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Modo de cuenta no válido: {mode}")
        service.settings = service.settings.model_copy(
            update={"ibkr": service.settings.ibkr.model_copy(update={"mode": new_mode})}
        )
        await service.switch_gateway(gateway_factory(new_mode))
        return RedirectResponse("/", status_code=303)

    # ---- watchlist -------------------------------------------------------------------------
    @app.get("/watchlist", response_class=HTMLResponse)
    async def watchlist(request: Request, message: str = ""):
        return render(request, "watchlist.html", no_autorefresh=True, tickers=service.watchlist.list(),
                      infos=service.ticker_info.all(), message=message, excluded=service.excluded)

    @app.post("/watchlist/allow")
    async def watchlist_allow(ticker: str = Form("")):
        """Quita un ticker de la lista de excluidos para que pueda volver a añadirse desde Universo."""
        ok = service.allow_ticker(ticker.strip().upper())
        msg = f"{ticker} ya puede volver a añadirse a la watchlist" if ok else f"{ticker} no estaba excluido"
        return RedirectResponse(f"/watchlist?{urlencode({'message': msg})}", status_code=303)

    async def apply_watchlist(parsed, mode: str) -> str:
        """Añade los tickers a la watchlist o, con mode='replace', la sustituye por ellos."""
        rejected = f", {len(parsed.rejected)} rechazados" if parsed.rejected else ""
        if parsed.rejected:
            rejected += ": " + ", ".join(t for t, _ in parsed.rejected)
        if skipped := service.excluded_among(parsed):
            rejected += f"; excluidos por inservibles (no se añaden): {', '.join(skipped)}"
        if mode == "replace":
            try:
                new, kept, removed = await service.replace_watchlist(parsed)
            except ValueError as exc:
                return f"Error: {exc}; no se ha cambiado la watchlist"
            gone = f" ({', '.join(removed)})" if removed else ""
            return (f"Watchlist sustituida: {len(new)} nuevos, {len(kept)} conservados, "
                    f"{len(removed)} quitados{gone}, {parsed.duplicates} repetidos{rejected}")
        new = await service.add_watchlist(parsed)
        return f"{len(new)} nuevos, {parsed.duplicates} repetidos{rejected or ', 0 rechazados'}"

    # ---- Universo (ficheros .xlsx de RankedStocks y HelloStocks elegidos por el usuario) ------
    @app.get("/universe", response_class=HTMLResponse)
    async def universe(request: Request, src: str = ALL, message: str = ""):
        if (back := recall(request, "universe")) is not None:
            return back
        sources = service.universe_sources
        numbers = {s.name: n for n, s in enumerate(sources, 1)}           # número de cada fuente
        current = next((s for s in sources if s.name == src), None)
        merged = merge(sources) if sources else None
        table = current.table if current else merged
        member: dict[str, list[int]] = {}
        for s_ in sources:
            for r in s_.table.rows:
                member.setdefault(r.ticker, []).append(numbers[s_.name])
        rows = list(table.rows) if table is not None else []
        # filtros de calidad de la empresa (ANTES de decidir qué entra en la watchlist)
        qp, qcfg, base = request.query_params, service.settings.scanner.quality, service.criteria()
        qform, criteria, qerror = quality_form(base), base, None
        if "submitted" in qp:
            try:
                overrides: dict = {}
                read_quality(qp, qform, overrides, qcfg, earnings=False, liquidity=False, solvency=True)
                criteria = base.with_filters(**overrides)
            except ValueError as exc:
                qerror = f"Parámetro no válido: {exc}"
        qinfos = service.quality.all()
        total_rows, excluded = len(rows), 0
        sectors = {t: i.sector for t, i in service.ticker_info.all().items()}
        sector_col = next((i for i, c in enumerate(table.columns) if c.name.strip().lower() == "sector"), None) if table else None
        exempt_tickers: set[str] = set()
        kept = []
        for row in rows:
            info = replace(qinfos.get(row.ticker) or TickerInfo(row.ticker), sector=_sector_of(row, sector_col, sectors))
            if is_exempt(info, qcfg.exempt_sectors):
                exempt_tickers.add(row.ticker)
            if not criteria.ticker_quality_active or ticker_quality_reject(info, criteria, qcfg.exempt_sectors) is None:
                kept.append(row)
        excluded, rows = total_rows - len(kept), kept
        with_quality = sum(1 for r in rows if r.ticker in qinfos)
        files = [(name, at, [s.name for s in srcs]) for name, (at, srcs) in service.universe_files.items()]
        return render(request, "universe.html", no_autorefresh=True, table=table, rows=rows, message=message,
                      qp=request.query_params, src=current.name if current else ALL, sources=sources, files=files,
                      numbers=numbers, member={t: ", ".join(map(str, n)) for t, n in member.items()},
                      all_count=len(member), manual_count=len(service.manual_tickers), in_watchlist=set(service.watchlist.list()),
                      qform=qform, qerror=qerror, qinfos=qinfos, excluded=excluded, total_rows=total_rows,
                      with_quality=with_quality, quality_active=criteria.ticker_quality_active,
                      quality_opts=dict(
                          quarters=qcfg.positive_quarters_options,
                          mcaps=[(_fmt(m), _mcap_label(m)) for m in qcfg.market_cap_options_m],
                          liquidity=qcfg.liquidity_options, leverage=[_fmt(v) for v in qcfg.leverage_options],
                          edgar=bool(service.settings.edgar.contact.strip())),
                      solvency=solvency_controls(qcfg, qform), levels=list(qcfg.level_labels.items()),
                      exempt_tickers=exempt_tickers, exempt_names=", ".join(qcfg.exempt_sectors))

    @app.post("/universe/load")
    async def universe_load(files: list[UploadFile]):
        done, errors, notes = [], [], ""
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
            service.set_universe_file(name, sources, content)   # queda guardado hasta que se quite o se cargue otro igual
            done.append(f"{name} ({len(sources)} fuente{'s' if len(sources) != 1 else ''}, {sum(len(s.table.rows) for s in sources)} filas)"
                        + (f", {removed} tickers sin opciones descartados" if removed else ""))
            if not checked:
                notes = "Aviso: no se pudo comprobar qué tickers tienen opciones; se han cargado todos"
        remembered.pop("universe", None)   # otras columnas: los filtros anteriores no valen
        msg = "Cargado: " + ", ".join(done) if done else ""
        if notes:
            msg += (" · " if msg else "") + notes
        if errors:
            msg += (" · " if msg else "") + "Error: " + "; ".join(errors)
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    @app.post("/universe/manual")
    async def universe_manual(text: str = Form("")):
        """Añade tickers escritos a mano a la fuente «Manual» (solo los que no están ya en otra fuente)."""
        parsed = parse_text(text)
        log.info("Universo: tickers manuales recibidos: %s", parsed.tickers)
        res = await service.add_manual_tickers(parsed)
        parts = []
        if res["added"]:
            parts.append(f"Añadidos a Manual: {', '.join(res['added'])}")
        if res["already"]:
            parts.append("Ya incluidos: " + ", ".join(f"{t} ({' · '.join(n)})" for t, n in res["already"].items()))
        if res["no_options"]:
            parts.append(f"Sin opciones (no añadidos): {', '.join(res['no_options'])}")
        if parsed.rejected:
            parts.append("Rechazados: " + ", ".join(t for t, _ in parsed.rejected))
        if not res["checked"] and res["added"]:
            parts.append("Aviso: no se pudo comprobar si tienen opciones")
        msg = " · ".join(parts) or "No hay ningún ticker que añadir"
        remembered.pop("universe", None)
        return RedirectResponse(f"/universe?{urlencode({'message': msg})}", status_code=303)

    @app.post("/universe/manual/clear")
    async def universe_manual_clear():
        n = service.clear_manual()
        remembered.pop("universe", None)
        return RedirectResponse(f"/universe?{urlencode({'message': f'Fuente Manual vaciada ({n} tickers)'})}", status_code=303)

    @app.post("/universe/remove")
    async def universe_remove(file: str = Form(...)):
        service.remove_universe_file(file)
        remembered.pop("universe", None)
        return RedirectResponse(f"/universe?{urlencode({'message': f'{file} quitado del universo'})}", status_code=303)

    @app.post("/universe/apply")
    async def universe_apply(request: Request):
        """Añade a la watchlist (o la sustituye por) los tickers marcados."""
        form = await request.form()
        mode = str(form.get("mode", "add"))
        msg = await apply_watchlist(parse_tokens([str(t) for t in form.getlist("sel")]), mode)
        return RedirectResponse(f"/watchlist?message={msg}", status_code=303)

    # ---- scanner y simulador ---------------------------------------------------------------
    def parse_scan(qp) -> dict:
        """Lee el formulario del scanner (GET o POST). Devuelve form, criteria, error y avisos."""
        submitted = "submitted" in qp
        cand = service.settings.scanner.candidates
        # el descuento máximo no es editable: es siempre el máximo del rango guardado (scanner.candidates)
        base = service.criteria().with_filters(strike_below_pct_max=cand.strike_below_pct_max)
        form = {
            "discount": _fmt(base.strike_below_pct_min),
            "dte_min": str(base.dte_min), "dte_max": str(base.dte_max),
            "min_yield": _fmt(base.min_annual_yield_pct),
            "ref": base.price_reference.value, "ref_x": _fmt(base.price_spread_pct),
            "trend_dir": base.trend_direction, "trend_method": base.trend_method,
            "trend_window": str(base.trend_window_months),
            "trend_days": str(base.trend_min_days), "support": base.require_support,
            "touch": "" if base.min_days_since_touch is None else str(base.min_days_since_touch),
            "price_min": _fmt(base.min_price) if base.min_price is not None else "",
            "price_max": _fmt(base.max_price) if base.max_price is not None else "",
            **quality_form(base),
            **{k: getattr(base, k) for k in MA_LINES},
            "ma_frame": base.ma_frame,
            **{k: getattr(base, k) for k in MA_CROSSES},
        }
        tcfg = service.settings.scanner.technical
        optional = {
            "oi": ("min_oi", int, base.min_oi),
            "bidsize": ("min_bid_size", int, base.min_bid_size),
            "spread": ("max_spread_pct", float, base.max_spread_pct),
            "ivr": ("min_iv_rank", float, base.min_iv_rank),
            "ivp": ("min_iv_percentile", float, base.min_iv_percentile),
        }
        fv = service.settings.scanner.filter_values
        shown = {"oi": fv.min_oi, "bidsize": fv.min_bid_size, "spread": fv.max_spread_pct,
                 "ivr": fv.min_iv_rank, "ivp": fv.min_iv_percentile}
        for key, (_, _, cfg_value) in optional.items():   # estado inicial desde la configuración
            form[f"use_{key}"] = cfg_value is not None
            # desmarcado: la caja muestra su valor por defecto (marcarlo lo aplica)
            form[key] = _fmt(cfg_value if cfg_value is not None else shown[key])

        criteria, error, warnings = None, None, []
        try:
            overrides: dict = {}
            if submitted:
                for key in ("discount", "dte_min", "dte_max", "min_yield"):
                    form[key] = qp.get(key, "").strip()
                form["ref"] = qp.get("ref", form["ref"])
                form["ref_x"] = qp.get("ref_x", form["ref_x"]).strip()   # ausente = valor de la configuración
                for key, field_name, allowed in (
                    ("trend_dir", "trend_direction", ("off", "up", "down")),
                    ("trend_method", "trend_method", ("low", "swings")),
                    *((k, k, ("any", "above", "below")) for k in MA_LINES),
                    ("ma_frame", "ma_frame", ("daily", "weekly", "monthly")),
                    *((k, k, ("any", "gte", "lte")) for k in MA_CROSSES),
                ):
                    form[key] = qp.get(key, form[key])
                    if form[key] not in allowed:
                        raise ValueError(f"valor desconocido en «{key}»")
                    overrides[field_name] = form[key]
                form["trend_window"] = qp.get("trend_window", form["trend_window"]).strip()
                overrides["trend_window_months"] = _required(form["trend_window"], int, "Ventana de la tendencia")
                if overrides["trend_window_months"] not in tcfg.trend_windows_months:
                    raise ValueError("ventana de la tendencia no permitida")
                form["trend_days"] = qp.get("trend_days", form["trend_days"]).strip()
                overrides["trend_min_days"] = _required(form["trend_days"], int, "Antigüedad del mínimo")
                if overrides["trend_min_days"] not in tcfg.trend_durations:
                    raise ValueError("antigüedad del mínimo no permitida")
                form["support"] = "support" in qp
                overrides["require_support"] = form["support"]
                for key, field_name, label in (("price_min", "min_price", "Precio mín."), ("price_max", "max_price", "Precio máx.")):
                    form[key] = qp.get(key, "").strip()
                    overrides[field_name] = _required(form[key], float, label) if form[key] else None
                    if overrides[field_name] is not None and overrides[field_name] < 0:
                        raise ValueError(f"{label} no puede ser negativo")
                if None not in (overrides["min_price"], overrides["max_price"]) and overrides["min_price"] > overrides["max_price"]:
                    raise ValueError("el precio mínimo no puede superar el máximo")
                read_quality(qp, form, overrides, service.settings.scanner.quality, earnings=True)
                form["touch"] = qp.get("touch", "").strip()
                overrides["min_days_since_touch"] = _required(form["touch"], int, "Días desde el último toque") if form["touch"] else None
                if overrides["min_days_since_touch"] is not None and overrides["min_days_since_touch"] not in tcfg.touch_min_days_options:
                    raise ValueError("días desde el último toque no permitidos")
                for key in optional:
                    form[f"use_{key}"] = f"use_{key}" in qp
                    form[key] = qp.get(key, "").strip()
                overrides["strike_below_pct_min"] = _required(form["discount"], float, "Descuento mín. del strike")
                overrides["min_annual_yield_pct"] = _required(form["min_yield"], float, "Yield anual mín.")
                overrides["dte_min"] = _required(form["dte_min"], int, "DTE mín.")
                overrides["dte_max"] = _required(form["dte_max"], int, "DTE máx.")
                try:
                    overrides["price_reference"] = PriceReference(form["ref"])
                except ValueError:
                    raise ValueError("precio de referencia desconocido") from None
                if overrides["price_reference"] is PriceReference.BID_PLUS_SPREAD:
                    overrides["price_spread_pct"] = _required(form["ref_x"], float, "X (% del spread)")
                    if not (0 <= overrides["price_spread_pct"] <= 100):
                        raise ValueError("X (% del spread) debe estar entre 0 y 100")
                lo, hi = overrides["strike_below_pct_min"], base.strike_below_pct_max
                if not (0 <= lo < 100):
                    raise ValueError("el descuento del strike debe estar entre 0 y 100")
                if lo > hi:
                    raise ValueError(f"el descuento mínimo no puede superar el máximo del rango guardado ({hi:g} %)")
                if overrides["min_annual_yield_pct"] < 0:
                    raise ValueError("el yield mínimo no puede ser negativo")
                if overrides["dte_min"] < 0 or overrides["dte_min"] > overrides["dte_max"]:
                    raise ValueError("los DTE deben cumplir 0 ≤ mín. ≤ máx.")
            for key, (field_name, cast, _) in optional.items():
                # sin marcar = filtro desactivado, aunque la configuración tenga un valor
                overrides[field_name] = _required(form[key], cast, field_name) if form[f"use_{key}"] else None
            criteria = base.with_filters(**overrides)
            if criteria.strike_below_pct_min < cand.strike_below_pct_min:
                warnings.append(f"Descuento mín. por debajo del rango guardado ({cand.strike_below_pct_min:g}%): "
                                "no hay contratos con menos descuento.")
            if criteria.dte_max > cand.dte_max or criteria.dte_min < cand.dte_min:
                warnings.append(f"DTE fuera del rango guardado ({cand.dte_min}–{cand.dte_max} días): "
                                "no hay contratos fuera de él.")
        except ValueError as exc:
            error = f"Parámetro no válido: {exc}"
        return dict(form=form, criteria=criteria, error=error, warnings=warnings)

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner(request: Request, debug: int = 0):
        if (back := recall(request, "scanner")) is not None:
            return back
        parsed = parse_scan(request.query_params)
        out = service.scan(parsed["criteria"], include_rejections=bool(debug)) if parsed["criteria"] else None
        c = parsed["criteria"]
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
        cand = service.settings.scanner.candidates
        presets = [dict(name=p.name, discount=_fmt(p.strike_below_pct_min), dte_min=p.dte_min,
                        dte_max=p.dte_max if p.dte_max is not None else cand.dte_max,
                        min_yield=_fmt(p.min_annual_yield_pct)) for p in service.settings.scanner.presets]
        return render(request, "scanner.html", no_autorefresh=True, out=out, ref_label=ref_label, watch_data=True, presets=presets,
                      candidates=service.settings.scanner.candidates,
                      margin=service.settings.scanner.catalog_margin_pct,
                      tech_opts=dict(
                          windows=[(m, f"{m} {'mes' if m == 1 else 'meses'}") for m in service.settings.scanner.technical.trend_windows_months],
                          durations=[(d, _days_label(d)) for d in service.settings.scanner.technical.trend_durations],
                          touches=[(d, _days_label(d)) for d in service.settings.scanner.technical.touch_min_days_options]),
                      quality_opts=dict(
                          quarters=service.settings.scanner.quality.positive_quarters_options,
                          mcaps=[(_fmt(m), _mcap_label(m)) for m in service.settings.scanner.quality.market_cap_options_m],
                          liquidity=service.settings.scanner.quality.liquidity_options,
                          leverage=[_fmt(v) for v in service.settings.scanner.quality.leverage_options],
                          edgar=bool(service.settings.edgar.contact.strip())),
                      report=service.state.last_refresh_report, **parsed)

    @app.get("/data-version")
    async def data_version():
        """Versión de los datos guardados: el scanner la consulta y se recarga
        si cambia (RF: refresco automático de la tabla al terminar una cotización)."""
        return {"version": service.state.data_version, "busy": service.busy}

    @app.get("/simulate")
    async def simulate_get():
        """La simulación es un POST: un GET (recargar, volver atrás) lleva de nuevo al scanner."""
        return RedirectResponse("/scanner", status_code=303)

    @app.post("/simulate", response_class=HTMLResponse)
    async def simulate(request: Request):
        form = await request.form()
        selected: list[SelectedContract] = []
        for raw in form.getlist("sel"):
            ticker, expiry, strike = str(raw).split("|")
            qty = int(str(form.get(f"qty_{raw}", "1")) or 1)
            selected.append(SelectedContract(ticker, date.fromisoformat(expiry), float(strike), max(1, qty)))
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
