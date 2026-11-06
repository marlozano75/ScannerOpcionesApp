"""Informe «Criba teórica» (`/scanner/criba`): tickers del Universo que, con su precio y su IV, no podrían llegar al yield mínimo de ningún perfil de puts.
Solo informa: no quita nada de las fuentes ni de la Watchlist (`scanner/screen.py`)."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from scanner_opciones.app.service import norm_ticker
from scanner_opciones.scanner import screen, yield_history as yh
from scanner_opciones.ui.common import MAX_FORM_FIELDS

LIMIT = 600


def build(service, tolerance: float) -> dict:
    """Contexto del informe: filas ordenadas de más a menos descartable, recuentos por estado y avisos de lo que no se evalúa."""
    specs = service.profile_specs()
    put_profiles = [(name, put) for name, put, _ in specs if put is not None]
    non_put = [name for name, put, spread in specs if put is None and spread is not None]
    infos = service.ticker_info.all()
    modes = service.ticker_modes()
    sources = service.ticker_lists()
    rows, missing = [], 0
    for ticker, names in sources.items():
        info = infos.get(norm_ticker(ticker))
        if info is None:
            missing += 1
            continue
        m = modes.get(norm_ticker(ticker)) or modes.get(ticker)
        protected = tuple(label for label, on in (("spread", bool(m and m.put_spread)), ("CDS", bool(m and m.cds))) if on)
        rows.append((screen.screen_ticker(ticker, info, put_profiles, protected, tolerance), tuple(names)))
    per_profile = screen.profile_counts([i for i, _ in ((infos.get(norm_ticker(t)), n) for t, n in sources.items()) if i is not None], put_profiles, tolerance)
    counts = {s: sum(1 for r, _ in rows if r.status == s) for s in (screen.OK, screen.DOUBTFUL, screen.DISCARD, screen.NO_DATA)}
    discard = sorted((x for x in rows if x[0].status == screen.DISCARD), key=lambda x: x[0].best.margin)
    return dict(counts=counts, discard=discard[:LIMIT], discard_total=len(discard), kept_by_modes=sum(1 for r, _ in discard if r.protected_by),
                missing=missing, per_profile=per_profile, profiles=[n for n, _ in put_profiles], non_put=non_put, tolerance=tolerance, limit=LIMIT,
                modes_on=service.settings.scanner.ticker_modes.enabled, total=len(sources))


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(float(str(value).replace(",", "."))), low), high)
    except (TypeError, ValueError):
        return default


def history_params(service, qp) -> dict:
    """Parámetros del paso de velas leídos de la URL o del formulario: perfil de puts (da el descuento y el yield exigido), meses, aciertos, meses con dato y liquidez."""
    puts = [(name, c) for name, c, _ in service.profile_specs() if c is not None]
    wanted = qp.get("hp", "")
    chosen = next((p for p in puts if p[0] == wanted), None) or next((p for p in puts if p[1].min_yield_pct > 0), puts[0] if puts else None)
    months = _int(qp.get("hm", 3), 3, 1, 12)
    return dict(profile=chosen[0] if chosen else "", discount=chosen[1].strike_below_pct_min if chosen else 5.0,
                required=chosen[1].min_yield_pct if chosen else 1.0, max_spread=chosen[1].max_spread_pct if chosen else None,
                min_oi=chosen[1].min_oi if chosen else None, months=months, hits=_int(qp.get("hh", 2), 2, 1, months),
                min_months=_int(qp.get("hn", 2), 2, 1, months), liquidity=_int(qp.get("hl", 2), 2, 0, 4), profiles=[n for n, _ in puts])


def history_context(service, qp) -> dict:
    p = history_params(service, qp)
    if not p["profile"]:
        return dict(h=p, h_counts={}, h_rows=[], h_total=0, h_stored=service.bid_history.count(), h_progress=service.yield_history_progress,
                    h_result=service.yield_history_result, h_limit=LIMIT)
    verdicts = service.yield_history_report(p["discount"], p["required"], p["months"], p["hits"], p["min_months"], p["liquidity"] or None,
                                             p["max_spread"], p["min_oi"])
    counts = {s: sum(1 for v in verdicts if v.status == s) for s in (yh.OK, yh.DISCARD, yh.NO_DATA, yh.PENDING)}
    sources = service.ticker_lists()
    rows = sorted((v for v in verdicts if v.status == yh.DISCARD), key=lambda v: (v.best_pct or 0.0, v.ticker))
    return dict(h=p, h_counts=counts, h_total=len(verdicts), h_stored=service.bid_history.count(), h_progress=service.yield_history_progress,
                h_result=service.yield_history_result, h_limit=LIMIT,
                h_rows=[(v, tuple(sources.get(v.ticker, ()))) for v in rows[:LIMIT]])


def register(app, service, render) -> None:
    @app.get("/scanner/criba", response_class=HTMLResponse)
    async def criba(request: Request, tol: float = screen.TOLERANCE):
        tol = min(max(tol, 1.0), 10.0)
        message = request.query_params.get("message", "")
        return render(request, "criba.html", no_autorefresh=True, message=message, **build(service, tol), **history_context(service, request.query_params))

    @app.post("/scanner/criba/historico")
    async def criba_history(request: Request):
        """Pide a tastytrade, en segundo plano, los bids históricos que faltan para los parámetros del formulario y vuelve al informe."""
        form = await request.form(max_fields=MAX_FORM_FIELDS)
        qp = {k: str(v) for k, v in form.items()}
        p = history_params(service, qp)
        if service.yield_history_progress is not None:
            msg = "Ya hay una petición de bids en curso."
        elif service.option_history is None:
            msg = "No hay proveedor de histórico (hace falta tastytrade)."
        elif not p["profile"]:
            msg = "No hay perfiles de puts guardados."
        else:
            detail = p["max_spread"] is not None or p["min_oi"] is not None      # solo si el perfil exige spread u OI: son dos peticiones de velas más por contrato
            service.launch(service.fetch_yield_history(p["discount"], p["months"], p["liquidity"] or None, detail))
            msg = "Pidiendo " + ("los bids, ask, OI e IV" if detail else "los bids") + " a tastytrade en segundo plano: el avance se muestra aquí y se actualiza solo."
        keep = {k: v for k, v in qp.items() if k in ("hp", "hm", "hh", "hn", "hl")}
        return RedirectResponse("/scanner/criba?" + urlencode({**keep, "message": msg}) + "#historico", status_code=303)
