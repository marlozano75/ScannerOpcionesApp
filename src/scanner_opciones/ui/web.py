"""Interfaz web local (FastAPI + Jinja2). Sin lógica de negocio: solo llama al AppService."""
from __future__ import annotations

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
from scanner_opciones.rankedstocks.filters import apply_filters, parse_filters
from scanner_opciones.rankedstocks.loader import load_table
from scanner_opciones.watchlist.loader import load_watchlist_file
from scanner_opciones.watchlist.parser import parse_text, parse_tokens

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

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        base = dict(
            state=service.state, mode=service.settings.ibkr.mode.value, busy=service.busy,
            refresh_minutes=service.settings.refresh_interval_minutes,
            delay_minutes=service.settings.ibkr.delay_minutes,
            pacing_wait=service.pacing_wait_seconds() if service.busy else 0,
            market_closed=service.settings.market.pause_when_closed and not service.market_open(),
            next_open=service.market.next_open(service.now()),
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

    @app.post("/daily")
    async def daily(revalidate: str = Form("")):
        """Fuerza la actualización de todos los tickers en segundo plano. Espera su turno si hay
        otra tarea en curso (antes se omitía en silencio). `revalidate`: vuelve a validar también
        las combinaciones strike/vencimiento que IBKR no listaba."""
        if not service.state.connected:
            return RedirectResponse("/watchlist?message=Sin conexión con TWS: no se puede actualizar", status_code=303)
        tickers = service.watchlist.list()
        service.launch(service.run_daily_then_refresh(tickers, wait=True, revalidate=bool(revalidate)))
        note = "en cola: hay otra tarea en curso" if service.busy else "en curso"
        return RedirectResponse(f"/watchlist?message=Actualización diaria de {len(tickers)} tickers {note}", status_code=303)

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
        return render(request, "watchlist.html", tickers=service.watchlist.list(),
                      infos=service.ticker_info.all(), message=message)

    async def apply_watchlist(parsed, mode: str) -> str:
        """Añade los tickers a la watchlist o, con mode='replace', la sustituye por ellos."""
        rejected = f", {len(parsed.rejected)} rechazados" if parsed.rejected else ""
        if parsed.rejected:
            rejected += ": " + ", ".join(t for t, _ in parsed.rejected)
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

    @app.post("/watchlist/paste")
    async def watchlist_paste(request: Request, text: str = Form(""), mode: str = Form("add")):
        msg = await apply_watchlist(parse_text(text), mode)
        return RedirectResponse(f"/watchlist?message={msg}", status_code=303)

    @app.post("/watchlist/upload")
    async def watchlist_upload(request: Request, file: UploadFile, mode: str = Form("add")):
        suffix = Path(file.filename or "").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            path = Path(tmp.name)
        try:
            parsed = load_watchlist_file(path)
        except WatchlistError as exc:
            return RedirectResponse(f"/watchlist?message=Error: {exc}", status_code=303)
        finally:
            path.unlink(missing_ok=True)
        msg = await apply_watchlist(parsed, mode)
        return RedirectResponse(f"/watchlist?message={msg}", status_code=303)

    # ---- RankedStocks (fichero .xlsx elegido por el usuario) ---------------------------------
    @app.get("/rankedstocks", response_class=HTMLResponse)
    async def rankedstocks(request: Request, message: str = ""):
        table = service.rankedstocks
        rows, error = [], None
        if table is not None:
            try:
                rows = apply_filters(table, parse_filters(table, request.query_params))
            except ValueError as exc:
                error, rows = f"Filtro no válido: {exc}", list(table.rows)
        return render(request, "rankedstocks.html", table=table, rows=rows, error=error, message=message,
                      qp=request.query_params, loaded_at=service.rankedstocks_loaded_at,
                      in_watchlist=set(service.watchlist.list()))

    @app.post("/rankedstocks/load")
    async def rankedstocks_load(file: UploadFile):
        suffix = Path(file.filename or "").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            path = Path(tmp.name)
        try:
            table = load_table(path)
        except WatchlistError as exc:
            return RedirectResponse(f"/rankedstocks?message=Error: {exc}", status_code=303)
        finally:
            path.unlink(missing_ok=True)
        table = replace(table, source=file.filename or table.source)   # el nombre real, no el del temporal
        service.rankedstocks, service.rankedstocks_loaded_at = table, service.now()
        return RedirectResponse(f"/rankedstocks?message={len(table.rows)} filas cargadas de {table.source}", status_code=303)

    @app.post("/rankedstocks/apply")
    async def rankedstocks_apply(request: Request):
        """Añade a la watchlist (o la sustituye por) los tickers marcados."""
        form = await request.form()
        mode = str(form.get("mode", "add"))
        msg = await apply_watchlist(parse_tokens([str(t) for t in form.getlist("sel")]), mode)
        return RedirectResponse(f"/watchlist?message={msg}", status_code=303)

    @app.post("/watchlist/remove")
    async def watchlist_remove(ticker: str = Form(...)):
        service.remove_ticker(ticker)
        return RedirectResponse(f"/watchlist?message={ticker} quitado, con sus contratos", status_code=303)

    # ---- scanner y simulador ---------------------------------------------------------------
    def parse_scan(qp) -> dict:
        """Lee el formulario del scanner (GET o POST). Devuelve form, criteria, error y avisos."""
        submitted = "submitted" in qp
        base = service.criteria()
        cand = service.settings.scanner.candidates
        form = {
            "discount": _fmt(base.strike_below_pct_min), "discount_max": _fmt(base.strike_below_pct_max),
            "dte_min": str(base.dte_min), "dte_max": str(base.dte_max),
            "min_yield": _fmt(base.min_yield_pct),
            "ref": base.price_reference.value, "ref_x": _fmt(base.price_spread_pct),
        }
        optional = {
            "oi": ("min_oi", int, base.min_oi),
            "spread": ("max_spread_pct", float, base.max_spread_pct),
            "ivr": ("min_iv_rank", float, base.min_iv_rank),
            "ivp": ("min_iv_percentile", float, base.min_iv_percentile),
        }
        for key, (_, _, cfg_value) in optional.items():   # estado inicial desde la configuración
            form[f"use_{key}"] = cfg_value is not None
            form[key] = "" if cfg_value is None else _fmt(cfg_value)

        criteria, error, warnings = None, None, []
        try:
            overrides: dict = {}
            if submitted:
                for key in ("discount", "discount_max", "dte_min", "dte_max", "min_yield"):
                    form[key] = qp.get(key, "").strip()
                form["ref"] = qp.get("ref", form["ref"])
                form["ref_x"] = qp.get("ref_x", form["ref_x"]).strip()   # ausente = valor de la configuración
                for key in optional:
                    form[f"use_{key}"] = f"use_{key}" in qp
                    form[key] = qp.get(key, "").strip()
                overrides["strike_below_pct_min"] = _required(form["discount"], float, "Descuento mín. del strike")
                overrides["strike_below_pct_max"] = _required(form["discount_max"], float, "Descuento máx. del strike")
                overrides["min_yield_pct"] = _required(form["min_yield"], float, "Yield bruto mín.")
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
                lo, hi = overrides["strike_below_pct_min"], overrides["strike_below_pct_max"]
                if not (0 <= lo < 100 and 0 <= hi < 100):
                    raise ValueError("el descuento del strike debe estar entre 0 y 100")
                if lo > hi:
                    raise ValueError("el descuento mínimo no puede superar el máximo")
                if overrides["min_yield_pct"] < 0:
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
            if criteria.strike_below_pct_max > cand.strike_below_pct_max:
                warnings.append(f"Descuento máx. por encima del rango guardado ({cand.strike_below_pct_max:g}%): "
                                "no hay contratos con más descuento.")
            if criteria.dte_max > cand.dte_max or criteria.dte_min < cand.dte_min:
                warnings.append(f"DTE fuera del rango guardado ({cand.dte_min}–{cand.dte_max} días): "
                                "no hay contratos fuera de él.")
        except ValueError as exc:
            error = f"Parámetro no válido: {exc}"
        return dict(form=form, criteria=criteria, error=error, warnings=warnings)

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner(request: Request, debug: int = 0):
        parsed = parse_scan(request.query_params)
        out = service.scan(parsed["criteria"], include_rejections=bool(debug)) if parsed["criteria"] else None
        c = parsed["criteria"]
        ref_label = None
        if c is not None:
            ref_label = {
                PriceReference.BID: "bid",
                PriceReference.MID: "mid (media bid/ask)",
                PriceReference.BID_PLUS_SPREAD: f"bid + {c.price_spread_pct:g}% del spread",
            }[c.price_reference]
        return render(request, "scanner.html", out=out, ref_label=ref_label,
                      candidates=service.settings.scanner.candidates,
                      report=service.state.last_refresh_report, **parsed)

    @app.get("/contracts", response_class=HTMLResponse)
    async def contracts(request: Request):
        """Todos los contratos almacenados, con las mismas columnas que el resultado del scanner."""
        rows = service.stored_contracts()
        c = service.criteria()
        ref_label = {
            PriceReference.BID: "bid",
            PriceReference.MID: "mid (media bid/ask)",
            PriceReference.BID_PLUS_SPREAD: f"bid + {c.price_spread_pct:g}% del spread",
        }[c.price_reference]
        return render(request, "contracts.html", rows=rows, ref_label=ref_label,
                      quoted=sum(1 for r in rows if r.snapshot.updated_at is not None),
                      candidates=service.settings.scanner.candidates)

    @app.post("/scanner/refresh")
    async def scanner_refresh(request: Request):
        """Cotiza los contratos del rango pedido en el formulario y vuelve al scanner."""
        data = await request.form()
        parsed = parse_scan(data)
        if parsed["criteria"] is not None and service.state.connected:
            await service.refresh_scoped(parsed["criteria"])
        query = urlencode([(k, str(v)) for k, v in data.multi_items()])
        return RedirectResponse(f"/scanner?{query}", status_code=303)

    @app.post("/simulate", response_class=HTMLResponse)
    async def simulate(request: Request):
        form = await request.form()
        selected: list[SelectedContract] = []
        for raw in form.getlist("sel"):
            ticker, expiry, strike = str(raw).split("|")
            qty = int(str(form.get(f"qty_{raw}", "1")) or 1)
            selected.append(SelectedContract(ticker, date.fromisoformat(expiry), float(strike), max(1, qty)))
        if not selected:
            return render(request, "simulation.html", result=None, selected=[], error="Selecciona al menos un contrato")
        try:
            result = await service.simulate(selected)
        except ValueError as exc:
            return render(request, "simulation.html", result=None, selected=selected, error=str(exc))
        return render(request, "simulation.html", result=result, selected=selected, error=None,
                      sectors=sorted(set(result.before.weights_pct) | set(result.after.weights_pct)))

    return app
