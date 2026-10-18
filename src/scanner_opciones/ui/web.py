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
from markupsafe import Markup, escape

from pydantic import ValidationError

from scanner_opciones.app.service import SCANNER_USER_CONFIG, AppService, SelectedContract, norm_ticker
from scanner_opciones.marketdata.analysts import summary
from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.domain.enums import AccountMode, PriceReference, TrafficLight
from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.rankedstocks.loader import clean_ticker
from scanner_opciones.community.fit import STRATEGY_NAME
from scanner_opciones.community.ideas import idea_key
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.technical import strike_history
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, unavailable_ma_fields
from scanner_opciones.portfolio.management import manage_puts
from scanner_opciones.scanner.engine import ScanOutput
from scanner_opciones.tortuga.radar import passes as radar_passes
from scanner_opciones.tortuga.routines import ROUTINES
from scanner_opciones.scanner.impact import explain_empty
from scanner_opciones.portfolio.classify import FALLBACK as FALLBACK_PROFILE, dte_range
from scanner_opciones.scanner.profiles import Profile
from scanner_opciones.scanner.market import vix_advice
from scanner_opciones.scanner.quality import is_exempt, spread_universe_reject, ticker_quality_reject
from scanner_opciones.scanner.spread_check import COLUMNS as SPREAD_CHECK_COLS
from scanner_opciones.ui import dashboard_charts as dash, page_charts, viz
from scanner_opciones.ui.charts import strike_chart_html, strike_mini_svg
from scanner_opciones.rankedstocks.loader import SYMBOL_HEADERS, _plain
from scanner_opciones.universe.descriptions import describe, describe_manual
from scanner_opciones.universe.hellostocks_html import HTML_SUFFIXES, missing_strategies
from scanner_opciones.universe.sources import ALL, load_sources, merge
from scanner_opciones.watchlist.parser import parse_text, parse_tokens

log = logging.getLogger(__name__)
IDEAS_WAIT_SECONDS = 10     # lo que espera la pestaña Ideas a que terminen las cotizaciones en directo antes de mostrarse con lo que haya
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def own_qty(owned, ticker, expiry, strike):
    """Contratos que ya tienes de esa put (negativo = vendidos, positivo = comprados) o None; lo usan las plantillas."""
    return (owned or {}).get((norm_ticker(ticker), expiry, round(float(strike), 4)))


TEMPLATES.env.globals["own_qty"] = own_qty


def sec_flag(alerts, ticker):
    """⚠ junto al ticker si tiene presentaciones recientes ante la SEC de las que preceden a problemas (cuentas no fiables, cambio de auditor,
    exclusión de bolsa...); el texto de cada una sale al pasar el ratón."""
    items = (alerts or {}).get(norm_ticker(ticker))
    if not items:
        return ""
    tip = "Presentaciones recientes ante la SEC (mira antes de vender una put):\n" + "\n".join(a.text for a in items)
    return Markup(f' <span class="sec" title="{escape(tip)}">⚠</span>')


TEMPLATES.env.globals["sec_flag"] = sec_flag


def analyst_flag(changes, ticker):
    """▼ (bajadas de rating) o ▲ (subidas) de analistas en los últimos días junto al ticker; el detalle de cada cambio sale al pasar el ratón."""
    items = (changes or {}).get(norm_ticker(ticker))
    if not items:
        return ""
    mark, css, short = summary(items)
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


def _x(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.2f}x"


def _num(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def source_initials(name: str) -> str:
    """Iniciales de una fuente del Universo para las etiquetas de Ideas: «TortugaTrades_Dia» → TTD, «Quality Growth» → QG."""
    words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+|[A-Z]+(?![a-z])", name.replace("_", " "))
    return "".join(w[0] for w in words).upper()[:4] or name[:2].upper()


def _grp(kind: str, title: str, items=(), note: str = "") -> str:
    """Un grupo de un mensaje del Universo: `kind` (ok, del, skip, warn, err), título, elementos (tickers, ficheros) y nota."""
    return "|".join((kind, title.replace("|", "/"), "\t".join(str(i).replace("|", "/").replace("\t", " ") for i in items), note.replace("|", "/")))


def message_groups(message: str) -> list[dict]:
    """Interpreta un mensaje de `_grp` (uno por línea). Una línea sin formato se muestra como aviso informativo."""
    out = []
    for line in (message or "").splitlines():
        parts = line.split("|")
        if len(parts) == 4 and parts[0] in ("ok", "del", "skip", "warn", "err"):
            kind, title, items, note = parts
            out.append({"kind": kind, "title": title, "chips": [i for i in items.split("\t") if i], "note": note})
        elif line.strip():
            out.append({"kind": "err" if line.startswith("Error") else "info", "title": "", "chips": [], "note": line})
    return out


def _load_groups(raw: dict[str, list[str]], loaded, before: dict[str, list[str]]) -> list[str]:
    """Grupos del mensaje de una carga de fichero: por fuente, los tickers nuevos, los quitados (estaban en la fuente
    que se reemplaza y ya no vienen) y los conservados; y, aparte, los descartados por no tener opciones."""
    out = []
    after = {src.name: [r.ticker for r in src.table.rows] for src in loaded}
    for name, tickers in after.items():
        old = before.get(name)
        if old is None:
            out.append(_grp("ok", f"Fuente nueva: {name}", [], f"{len(tickers)} tickers"))
            continue
        fresh = [t for t in tickers if t not in set(old)]
        gone = [t for t in old if t not in set(tickers)]
        kept = len(tickers) - len(fresh)
        out.append(_grp("ok" if fresh else "skip", f"Nuevos en {name}", fresh[:60],
                        f"{len(fresh)} nuevos · {kept} conservados" + (f" · +{len(fresh) - 60} más" if len(fresh) > 60 else "")))
        if gone:
            out.append(_grp("del", f"Quitados de {name}", gone[:60], f"{len(gone)} quitados" if len(gone) > 60 else ""))
    no_opts = sorted({t for n, ts in raw.items() for t in ts} - {t for ts in after.values() for t in ts})
    if no_opts:
        out.append(_grp("warn", "Sin opciones (descartados)", no_opts[:60], f"{len(no_opts)} tickers" + (" · se muestran 60" if len(no_opts) > 60 else "")))
    return out


def _days_label(days: int) -> str:
    """7 -> «1 semana», 30 -> «1 mes», 365 -> «1 año», 252 -> «252 días (~1 año bursátil)»; otros valores, «N días»."""
    if days == 252:
        return "252 días (~1 año bursátil)"
    for size, one, many in ((365, "año", "años"), (30, "mes", "meses"), (7, "semana", "semanas")):
        if days % size == 0:
            n = days // size
            return f"{n} {one if n == 1 else many}"
    return f"{days} días"


# Filtros de calidad de la empresa: (campo del formulario, campo del criterio, tipo, lista de opciones, etiqueta)
QUALITY_SELECTS = (
    ("q_quarters", "min_positive_quarters", int, "positive_quarters_options", "Trimestres con beneficios"),
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
    ("q_roic", "min_roic", "roic", "ROIC", "≥", "%"),
    ("q_loss", "max_loss_years", "loss_years", "Años con pérdidas (últimos 10)", "≤", ""),
    ("q_revdrop", "max_revenue_drops", "revenue_drops", "Años con caída de ingresos (últimos 10)", "≤", ""),
    ("q_evol", "max_earnings_volatility", "earnings_volatility", "Volatilidad de los beneficios", "≤", "%"),
)
SOLVENCY_CORE = ("q_de", "q_cov", "q_cash", "q_ocfd")   # el bloque «Solvencia»; los otros tres son indicadores opcionales


def _threshold_text(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value * 100:g} %"
    if unit == "pct":
        return f"{value:g} %"
    return f"{value:g}{unit}"


def solvency_controls(qcfg, form: dict, counts: Optional[dict] = None) -> list[dict]:
    """Selectores de solvencia para la plantilla: cada opción lleva su umbral («Estándar (≤ 1)») y, si se dan los
    recuentos, cuántos tickers de la watchlist pasan ese grado («· 124/281»): así se ve lo restrictivo que es."""
    out = []
    for key, field_name, metric, label, sign, unit in SOLVENCY_UI:
        thresholds = getattr(qcfg.thresholds, metric)
        out.append(dict(
            key=key, label=label, core=key in SOLVENCY_CORE, value=form.get(key, ""),
            options=[(level, f"{text} ({sign} {_threshold_text(thresholds[level], unit)})"
                      + _count_text(counts, (field_name, thresholds[level])))
                     for level, text in qcfg.level_labels.items()],
        ))
    return out


def _count_text(counts: Optional[dict], key) -> str:
    """« · 124/281» para una opción de filtro, o nada si no hay recuentos."""
    if not counts or key not in counts["by_option"]:
        return ""
    return f" · {counts['by_option'][key]}/{counts['total']}"


def quality_counts(infos: dict, tickers: list[str], base, qcfg) -> dict:
    """Cuántos tickers de `tickers` pasan CADA opción de calidad por separado (los exentos por sector pasan; un dato que
    falta no pasa). Sirve para ver de antemano cuánto descarta una selección. Los filtros de calidad ven al ticker, no
    al contrato, así que el recuento es de tickers."""
    from scanner_opciones.domain.models import TickerInfo
    from scanner_opciones.scanner.quality import ticker_quality_reject

    def passing(**overrides) -> int:
        c = base.with_filters(**overrides)
        return sum(1 for t in tickers if ticker_quality_reject(infos.get(t) or TickerInfo(t), c, qcfg.exempt_entries) is None)

    by: dict = {("require_profitable", True): passing(require_profitable=True),
                ("require_positive_fcf", True): passing(require_positive_fcf=True),
                ("require_manageable_debt", True): passing(require_manageable_debt=True)}
    for n in qcfg.positive_quarters_options:
        by[("min_positive_quarters", n)] = passing(min_positive_quarters=n)
    for n in qcfg.liquidity_options:
        by[("min_option_liquidity", n)] = passing(min_option_liquidity=n)
    for v in qcfg.leverage_options:
        by[("max_liabilities_to_equity", float(v))] = passing(max_liabilities_to_equity=float(v))
    for _, field_name, metric, *_ in SOLVENCY_UI:
        for value in getattr(qcfg.thresholds, metric).values():
            by[(field_name, value)] = passing(**{field_name: value})
    return {"total": len(tickers), "by_option": by}


def quality_form(base) -> dict:
    """Valores del formulario de calidad a partir de los criterios (sin filtros por defecto)."""
    return {
        "q_profit": base.require_profitable, "q_earn": base.avoid_earnings, "q_fcf": base.require_positive_fcf,
        "q_manage": base.require_manageable_debt,
        "q_quarters": "" if base.min_positive_quarters is None else str(base.min_positive_quarters),
        "q_liq": "" if base.min_option_liquidity is None else str(base.min_option_liquidity),
        "q_lev": "" if base.max_liabilities_to_equity is None else _fmt(base.max_liabilities_to_equity),
        **{key: "" for key, *_ in SOLVENCY_UI},
    }


def read_quality(qp, form: dict, overrides: dict, qcfg, earnings: bool, liquidity: bool = True, solvency: bool = False,
                 company: bool = True) -> None:
    """Lee los filtros de calidad de la URL (scanner y Universo comparten parser). Cada valor se valida contra las
    listas permitidas de la configuración. `earnings`: el filtro de resultados es por contrato, solo existe en el scanner.
    `liquidity`: la liquidez de las opciones solo se filtra en el scanner (en el Universo es solo un indicador).
    `solvency`: los filtros de solvencia con grado de exigencia y el de «deuda baja o manejable» (Universo).
    `company`: los filtros de la empresa (beneficios, trimestres, apalancamiento, FCF): viven en el Universo; el
    scanner solo conserva los que dependen de las opciones (liquidez y resultados)."""
    if company:
        form["q_profit"], form["q_fcf"] = "q_profit" in qp, "q_fcf" in qp
        overrides["require_profitable"], overrides["require_positive_fcf"] = form["q_profit"], form["q_fcf"]
    if earnings:
        form["q_earn"] = "q_earn" in qp
        overrides["avoid_earnings"] = form["q_earn"]
    for key, field_name, cast, options, label in QUALITY_SELECTS:
        if (key == "q_liq" and not liquidity) or (key != "q_liq" and not company):
            continue
        form[key] = qp.get(key, "").strip()
        overrides[field_name] = _required(form[key], cast, label) if form[key] else None
        if overrides[field_name] is not None and overrides[field_name] not in getattr(qcfg, options):
            raise ValueError(f"{label} no permitida")
    if solvency:
        form["q_manage"] = "q_manage" in qp
        overrides["require_manageable_debt"] = form["q_manage"]
        for key, field_name, metric, label, _, _ in SOLVENCY_UI:
            level = qp.get(key, "").strip()
            if level and level not in qcfg.level_labels:
                raise ValueError(f"{label}: grado de exigencia no permitido")
            form[key] = level
            overrides[field_name] = getattr(qcfg.thresholds, metric)[level] if level else None


# parámetros del formulario que controla la estrategia: el botón los sustituye y respeta todos los demás filtros
STRATEGY_KEYS = frozenset((
    "discount", "dte_min", "dte_max", "min_yield", "use_delta", "delta_min", "delta_max", "use_near", "near_pct",
    "use_ivr", "ivr", "use_ivp", "ivp", "iv_any", "use_ivhv", "ivhv", "use_ivchg", "ivchg", "use_atr", "atr_mult", "use_exp", "exp_mult", "use_rsi", "signals",
    "rsi_max", "use_boll", "boll_pctb", "use_bw", "boll_width", "ma200", "q_earn", "q_liq", "reset", "debug"))


# Valores de la estrategia que se editan desde la aplicación: (grupo, [(campo, etiqueta, tipo)]); tipos: float, int, bool, ofloat/oint
# (opcional: vacío = None). El resto de campos de `StrategySettings` se conserva como está.
STRATEGY_FIELDS = (
    ("Strike, vencimiento y prima", (
        ("strike_below_pct_min", "Descuento mín. del strike (%)", "float"), ("dte_min", "DTE mín.", "int"), ("dte_max", "DTE máx.", "int"),
        ("min_yield_pct", "Yield mín. del periodo (%)", "float"))),
    ("Delta y volatilidad", (
        ("min_abs_delta", "|Delta| mín. (vacío = sin mínimo)", "ofloat"), ("max_abs_delta", "|Delta| máx.", "float"),
        ("min_iv_rank", "IV Rank mín.", "float"), ("min_iv_percentile", "IV Percentile mín.", "float"),
        ("iv_rank_pct_any", "Basta con IV Rank o IV Percentile (no los dos)", "bool"),
        ("min_iv_hv_ratio", "IV / HV30 mín.", "float"), ("max_iv_change_5d", "Cambio de IV a 5 días máx. (puntos)", "float"))),
    ("Señales de entrada", (
        ("min_signals", "Señales mínimas (0 = todas las marcadas)", "int"), ("near_level_pct", "Cerca de la media de 50 / soporte (%)", "float"),
        ("max_rsi_level", "Nivel de RSI tocado", "float"), ("max_bollinger_pctb", "Bollinger %B máx.", "float"),
        ("min_bollinger_width_pct", "Ancho de bandas mín. (%)", "float"), ("min_atr_multiple", "Strike a N × ATR", "float"),
        ("expected_move_mult", "Fuera de N × movimiento esperado", "float"))),
    ("Qué activa el botón", (
        ("use_rsi", "RSI rebotando", "bool"), ("use_bollinger", "Bollinger %B", "bool"), ("use_bollinger_width", "Ancho de bandas", "bool"),
        ("use_atr", "ATR", "bool"), ("use_expected_move", "Movimiento esperado", "bool"),
        ("price_above_ma200", "Precio sobre la MA200", "bool"), ("avoid_earnings", "Evitar resultados", "bool"),
        ("min_option_liquidity", "Liquidez de opciones mín. (1-5, vacío = no exigir)", "oint"))),
    ("Aviso del VIX (Panel)", (
        ("min_vix", "VIX: primas pobres por debajo de", "float"), ("ideal_vix_min", "VIX: ideal desde", "float"),
        ("max_vix", "VIX: nervioso por encima de", "float"))),
)


SPREAD_CHECK_MAX = 400   # filas máximas del panel «Revisión de spreads»
SPREAD_CHECK_COLUMNS = SPREAD_CHECK_COLS

VIX_FIELDS = (    # aviso del VIX del Panel (`scanner.strategy`): única parte de la estrategia que se edita en /scanner/config
    ("min_vix", "VIX: primas pobres por debajo de", "float"), ("ideal_vix_min", "VIX: ideal desde", "float"),
    ("max_vix", "VIX: nervioso por encima de", "float"),
)

SPREAD_FIELDS = (    # valores iniciales del modo Spread del Scanner (`scanner.spreads`); en el formulario van como `sp_<clave>`
    ("width_options", "Anchos que se prueban ($, separados por comas)", "floats"),
    ("min_return_on_risk_pct", "Retorno mín. sobre riesgo (%, hasta el vencimiento)", "float"),
    ("min_credit_usd", "Crédito mín. ($ por contrato)", "float"),
    ("max_combo_spread_pct", "Spread bid/ask máx. del combo (% del crédito)", "float"),
    ("min_long_oi", "OI mín. de la pata comprada", "int"),
    ("max_long_spread_pct", "Spread bid/ask máx. de la pata comprada (%)", "float"),
    ("max_loss_nlv_pct", "Pérdida máxima (% del NLV)", "float"),
    ("max_naked_strike", "Strike máximo de la put desnuda en «Ambos» ($)", "float"),
    ("usd_per_base", "Dólares por unidad de moneda base (vacío = el de IBKR)", "ofloat"),
)


def _read_num(raw, cast, label: str):
    text = str(raw or "").strip().replace(",", ".")
    try:
        return cast(text)
    except ValueError:
        raise ValueError(f"{label}: «{raw}» no es un número válido") from None


def parse_spread_config(form, current: dict) -> dict:
    """Lee los valores del modo Spread del formulario de edición (`sp_<clave>`); los campos que no vienen se conservan."""
    out = dict(current)
    for key, label, kind in SPREAD_FIELDS:
        raw = form.get(f"sp_{key}")
        if raw is None:
            continue
        if kind == "floats":
            out[key] = [_read_num(x, float, label) for x in str(raw).replace(";", ",").split(",") if x.strip()]
            if not out[key]:
                raise ValueError(f"{label}: indica al menos un ancho")
        elif kind == "ofloat":
            out[key] = _read_num(raw, float, label) if str(raw).strip() else None
        else:
            out[key] = _read_num(raw, float if kind == "float" else int, label)
    return out


def parse_scanner_config(form, current: dict) -> tuple[list[dict], dict]:
    """Lee el formulario de edición de las configuraciones (presets) y de la estrategia de venta de puts. `current` es la estrategia
    actual (dict): los campos que el formulario no lleva se conservan. Lanza `ValueError` con el campo erróneo."""
    presets = []
    for i in range(int(form.get("n_presets", "0") or 0)):
        name = str(form.get(f"p{i}_name", "")).strip()
        if not name:    # nombre vacío = quitar la configuración
            continue
        dte_max = str(form.get(f"p{i}_dte_max", "")).strip()
        presets.append(dict(
            name=name, strike_below_pct_min=_read_num(form.get(f"p{i}_discount"), float, f"{name}: descuento mín."),
            dte_min=_read_num(form.get(f"p{i}_dte_min"), int, f"{name}: DTE mín."),
            dte_max=_read_num(dte_max, int, f"{name}: DTE máx.") if dte_max else None,
            min_yield_pct=_read_num(form.get(f"p{i}_yield"), float, f"{name}: yield mín.")))
    strategy = dict(current)
    for _, fields in STRATEGY_FIELDS:
        for key, label, kind in fields:
            raw = form.get(f"s_{key}")
            if kind != "bool" and raw is None:     # el formulario no lo trae: se conserva
                continue
            if kind == "bool":
                strategy[key] = f"s_{key}" in form
            elif kind in ("ofloat", "oint"):
                strategy[key] = _read_num(raw, float if kind == "ofloat" else int, label) if str(raw or "").strip() else None
            else:
                strategy[key] = _read_num(raw, float if kind == "float" else int, label)
    return presets, strategy


def strategy_link(settings, form: dict, base, current=None) -> tuple[str, bool]:
    """Consulta del botón «Estrategia de venta de puts» (activa de una vez todos sus filtros, con los valores de
    `scanner.strategy`, y conserva el resto de filtros de `current`, la consulta actual) y si el formulario actual ya la tiene cargada."""
    st, fv = settings.scanner.strategy, settings.scanner.filter_values
    params = {
        "submitted": "1", "discount": _fmt(st.strike_below_pct_min), "dte_min": str(st.dte_min), "dte_max": str(st.dte_max),
        "min_yield": _fmt(st.min_yield_pct), "use_delta": "on", "delta_max": _fmt(st.max_abs_delta),
        "use_near": "on", "near_pct": _fmt(st.near_level_pct),
        "use_ivr": "on", "ivr": _fmt(st.min_iv_rank), "use_ivp": "on", "ivp": _fmt(st.min_iv_percentile),
        "signals": str(st.min_signals),
    }
    if st.min_abs_delta is not None:       # por defecto solo máximo: el mínimo de prima lo pone el yield
        params["delta_min"] = _fmt(st.min_abs_delta)
    if st.iv_rank_pct_any:
        params["iv_any"] = "on"
    params.update(use_ivhv="on", ivhv=_fmt(st.min_iv_hv_ratio), use_ivchg="on", ivchg=_fmt(st.max_iv_change_5d))
    for use, key, enabled, value in (("use_rsi", "rsi_max", st.use_rsi, st.max_rsi_level),
                                     ("use_boll", "boll_pctb", st.use_bollinger, st.max_bollinger_pctb),
                                     ("use_bw", "boll_width", st.use_bollinger_width, st.min_bollinger_width_pct)):
        if enabled:
            params[use], params[key] = "on", _fmt(value)
    if st.use_expected_move:
        params["use_exp"], params["exp_mult"] = "on", _fmt(st.expected_move_mult)
    if st.use_atr:
        params["use_atr"], params["atr_mult"] = "on", _fmt(st.min_atr_multiple)
    if st.price_above_ma200:
        params["ma200"] = "above"
    if st.avoid_earnings:
        params["q_earn"] = "on"
    if st.min_option_liquidity is not None:
        params["q_liq"] = str(st.min_option_liquidity)
    checks = ("discount", "dte_min", "dte_max", "min_yield", "delta_max", "near_pct", "ivr", "ivp", "ma200", "q_liq", "rsi_max", "boll_pctb",
              "boll_width", "ivhv", "ivchg", "atr_mult", "exp_mult", "signals")
    on = all(form.get(f) == params[f] for f in checks if f in params)
    on = on and form.get("delta_min", "") == params.get("delta_min", "")           # en blanco = sin mínimo
    on = on and all(form.get(f) for f in ("use_delta", "use_near", "use_ivr", "use_ivp", "use_ivhv", "use_ivchg",
                                          *(u for u in ("use_rsi", "use_boll", "use_bw", "use_atr", "use_exp") if u in params)))
    on = on and (not st.avoid_earnings or bool(form.get("q_earn")))
    on = on and bool(form.get("iv_any")) == st.iv_rank_pct_any
    kept = {k: v for k, v in (current.items() if current is not None else ()) if k not in STRATEGY_KEYS} if current is not None and "submitted" in current else {}
    return urlencode({**kept, **params}), on


def preset_link(settings, preset, form: dict, current=None) -> tuple[str, bool]:
    """Consulta de un perfil TPC (Mensual / Táctico): carga descuento, DTE y yield y SUSTITUYE los filtros propios de la estrategia
    Tastytrade (delta, IV Rank, señales, resultados…: `STRATEGY_KEYS`), de modo que los perfiles no se mezclan; el resto de filtros
    de `current` (precio de venta, calidad, tendencia…) se conserva. Devuelve también si el formulario ya lo tiene cargado."""
    cand, fv = settings.scanner.candidates, settings.scanner.filter_values
    params = {
        "submitted": "1", "discount": _fmt(preset.strike_below_pct_min), "dte_min": str(preset.dte_min),
        "dte_max": str(preset.dte_max if preset.dte_max is not None else cand.dte_max),
        "min_yield": _fmt(preset.min_yield_pct),
    }
    on = all(form.get(k) == params[k] for k in ("discount", "dte_min", "dte_max", "min_yield"))
    kept = {k: v for k, v in (current.items() if current is not None else ()) if k not in STRATEGY_KEYS} if current is not None and "submitted" in current else {}
    return urlencode({**kept, **params}), on


SEED_COLORS = {"Mensual (TPC)": "#2e7d32", "Táctico (TPC)": "#e08a00", "TastyPut": "#1f6feb", "TastySpread": "#0b8a8f", FALLBACK_PROFILE: "#7b5ea7"}


def seed_profiles(settings) -> tuple[list[Profile], str]:
    """Perfiles que salen de los presets TPC y de la estrategia Tastytrade (config.yaml o editados en /scanner/config) mientras no se
    guarden perfiles propios. TPC solo con put vendida; la estrategia Tastytrade se reparte en TastyPut (put vendida) y TastySpread
    (spread de puts). El inicial es TastyPut. Las puts se gestionan con TPC (mantener hasta el vencimiento) y los spreads con Tastytrade."""
    out = []
    for p in settings.scanner.presets:
        params = dict(parse_qsl(preset_link(settings, p, {})[0]))
        out.append(Profile(p.name, ("put",), management="tpc", color=SEED_COLORS.get(p.name, "")).with_filters("put", params).with_filters("spread", params))
    params = dict(parse_qsl(strategy_link(settings, {}, None)[0]))
    out.append(Profile("TastyPut", ("put",), management="tpc", color=SEED_COLORS["TastyPut"]).with_filters("put", params).with_filters("spread", params))
    out.append(Profile("TastySpread", ("spread",), color=SEED_COLORS["TastySpread"]).with_filters("put", params).with_filters("spread", params))
    out.append(seed_fallback(settings))
    return out, "TastyPut"


def seed_fallback(settings) -> Profile:
    """Perfil «Tortuga»: recoge las puts que no encajan por DTE en ningún otro; filtros básicos (los valores iniciales) y gestión Tastytrade."""
    ini = settings.scanner.initial
    params = {"discount": _fmt(ini.strike_below_pct_min), "dte_min": str(ini.dte_min), "dte_max": str(ini.dte_max), "min_yield": _fmt(ini.min_yield_pct)}
    return Profile(FALLBACK_PROFILE, ("put", "spread"), color=SEED_COLORS[FALLBACK_PROFILE]).with_filters("put", params).with_filters("spread", params)


def profile_link(p: Profile, mode: Optional[str] = None, shown: Optional[str] = None) -> str:
    """Consulta que carga el perfil en el Scanner (reemplaza todo el formulario): el juego de filtros que se ve va sin prefijo y el otro como `o_...`."""
    mode = mode if mode in ("put", "spread", "both") else p.mode
    shown = shown if shown in ("put", "spread") else ("spread" if mode == "spread" else "put")   # `shown`: la pestaña que se estaba viendo
    other = "put" if shown == "spread" else "spread"
    return urlencode({**p.set_params(shown), **{"o_" + k: v for k, v in p.set_params(other).items()},
                      "submitted": "1", "mode": mode, "profile": p.name, "set": shown})


# parámetros de la URL que no pertenecen a ningún juego de filtros
STRUCT_KEYS = frozenset(("submitted", "mode", "profile", "set", "debug", "reset", "message"))


def split_sets(qp) -> tuple[str, dict, dict]:
    """(juego visible, sus parámetros, parámetros del otro juego). El visible va sin prefijo; el otro como `o_...` (campos ocultos del formulario).
    Una consulta sin `o_...` (enlaces antiguos) trata el otro juego como una copia del visible."""
    items = dict(qp)
    shown = items.get("set") or ("put" if items.get("mode", "put") == "put" else "spread")
    shown = shown if shown in ("put", "spread") else "put"
    own = {k: v for k, v in items.items() if k not in STRUCT_KEYS and not k.startswith("o_")}
    other = {k[2:]: v for k, v in items.items() if k.startswith("o_") and k[2:] not in STRUCT_KEYS}
    return shown, own, (other or dict(own))


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


TEMPLATES.env.globals["viz"] = viz
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
            state=service.state, mode=service.settings.ibkr.mode.value,
            account_id=(service.state.account.account_id if service.state.account else service.settings.ibkr.account),
            account_alias=service.state.account.alias if service.state.account else None, busy=service.busy,
            refresh_minutes=service.settings.refresh_interval_minutes,
            delay_minutes=service.settings.ibkr.delay_minutes,
            pacing_wait=service.pacing_wait_seconds() if service.busy else 0,
            market_closed=service.settings.market.pause_when_closed and not service.market_open(),
            next_open=service.market.next_open(service.now()), path=request.url.path,
            excluded_notice=service.excluded_notice, sec_alerts=service.filing_alert_map(), analyst_alerts=service.analyst_map(),
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
                           service.put_assignment_notes())
        charts = dict(
            cushion_current=dash.cushion_meter(risk.current if risk else None, thresholds),
            cushion_look_ahead=dash.cushion_meter(risk.look_ahead if risk else None, thresholds),
            cushion_post=dash.cushion_meter(risk.post_expiration if risk else None, thresholds),
            exposure=dash.exposure_bars(assignment), sectors=dash.sector_bars(sectors), weeks=dash.week_bars(weeks),
            vix_history=dash.vix_history(vix) if vix else viz.empty(), vix_curve=curve, vix_shape=shape)
        return render(request, "dashboard.html", profile_names=[p.name for p in get_profiles()[0]], profile_colors={p.name: p.color for p in get_profiles()[0]}, sectors=sectors, weeks=weeks, assignment=assignment, charts=charts, vix_note=vix_note, mgmt=mgmt, mgmt_cfg=service.settings.management,
                      risk_cfg=thresholds,
                      all_sectors=sorted({s for w in weeks for s in w.amounts} | set(sectors.weights_pct)))

    @app.post("/management/profile")
    async def management_profile(request: Request):
        """Cambia el perfil del Scanner (y con él las reglas de gestión) de una put vendida del Panel."""
        form = await request.form()
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
        tickers, infos = service.watchlist.list(), service.ticker_info.all()
        return render(request, "watchlist.html", tickers=tickers, infos=infos, message=message, groups=message_groups(message),
                      ov=page_charts.watchlist_overview(tickers, infos))

    # ---- ideas de la comunidad ---------------------------------------------------------------
    @app.get("/ideas", response_class=HTMLResponse)
    async def ideas(request: Request, solo: int = 0, venc: int = 0, trader: str = "", strategy: str = "", message: str = ""):
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
        views = [v for v in everything
                 if (venc or v.status != "expired") and (not solo or v.in_watchlist)
                 and (not trader or v.idea.trader == trader) and (not strategy or v.idea.strategy == strategy)]
        return render(request, "ideas.html", data=data, owned=service.owned_options(), file_path=service.settings.community.path, views=views, total=len(everything),
                      fit_names=[p.name for p in service.settings.scanner.presets] + [STRATEGY_NAME],
                      valid=sum(1 for v in views if v.status == "valid"), solo=solo, venc=venc, trader=trader, strategy=strategy,
                      traders=sorted({v.idea.trader for v in everything}, key=str.casefold), stale=service.community_stale(),
                      auto_reload=service.market.is_open(service.now()), quoting=quoting, message=message, groups=message_groups(message),
                      keys=[idea_key(v.idea) for v in views], perm_name=service.settings.community.perm_source_name,
                      lists={t: [(source_initials(n), n) for n in names] for t, names in service.ticker_lists().items()})

    # ---- Tortuga: radar de Bull Put Spreads y rutinas -------------------------------------------
    @app.get("/tortuga", response_class=HTMLResponse)
    async def tortuga(request: Request, cumple: int = 0, solo: int = 0, message: str = ""):
        data = service.radar_spreads()
        minimum = service.radar_min_yield()
        watch = set(service.watchlist.list())
        held = {norm_ticker(p.ticker) for p in service.state.positions}
        items = [dict(row=r, ok=radar_passes(r, minimum), wl=r.ticker in watch, held=norm_ticker(r.ticker) in held)
                 for r in (data.rows if data else ())]
        shown = [i for i in items if (not cumple or i["ok"]) and (not solo or i["wl"])]
        runner = service.tortuga
        return render(request, "tortuga.html", data=data, items=shown, total=len(items), passing=sum(1 for i in items if i["ok"]),
                      minimum=minimum, cumple=cumple, solo=solo, cfg=service.settings.tortuga, message=message, groups=message_groups(message),
                      routines=ROUTINES, rstate=runner.state() if runner else {}, running=runner.running if runner else None,
                      enabled=runner is not None and service.settings.tortuga.enabled, busy_routines=service._tortuga_busy,
                      age_days=(service.now().date() - data.as_of).days if data and data.as_of else None)

    @app.post("/tortuga/run")
    async def tortuga_run(request: Request):
        """Lanza a mano una rutina de Tortuga (o todas las que toquen si no se indica) sin esperar a su hora."""
        form = await request.form()
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
        form = await request.form()
        back = {k: str(form.get(k)) for k in ("solo", "venc", "trader", "strategy") if form.get(k)}
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

    @app.post("/excluded/dismiss")
    async def excluded_dismiss():
        """Cierra el aviso de tickers sacados de la watchlist (la «x» del aviso)."""
        service.dismiss_excluded_notice()
        return Response(status_code=204)

    async def apply_watchlist(parsed, mode: str) -> str:
        """Añade los tickers a la watchlist o, con mode='replace', la sustituye por ellos. Devuelve un mensaje de `_grp`."""
        def tail(new: list[str], kept: Optional[list[str]] = None, removed=()) -> str:
            out = []
            if new:
                out.append(_grp("ok", "Nuevos en la watchlist", new, "se actualizan en segundo plano (progreso arriba)" if service.state.connected else ""))
            else:
                out.append(_grp("skip", "Sin tickers nuevos", [], ""))
            if kept is not None:
                out.append(_grp("skip", "Conservados", [], f"{len(kept)} ticker{'s' if len(kept) != 1 else ''}"))
            if removed:
                out.append(_grp("del", "Quitados de la watchlist", removed))
            if parsed.duplicates:
                out.append(_grp("skip", "Repetidos", [], f"{parsed.duplicates} en la lista"))
            if parsed.rejected:
                out.append(_grp("err", "Rechazados", [t for t, _ in parsed.rejected]))
            return "\n".join(out)
        if mode == "replace":
            try:
                new, kept, removed = await service.replace_watchlist(parsed, background=True)
            except ValueError as exc:
                return _grp("err", "Error", [], f"{exc}; no se ha cambiado la watchlist")
            return _grp("ok", "Watchlist sustituida", [], "") + "\n" + tail(new, kept, removed)
        new = await service.add_watchlist(parsed, background=True)
        return tail(new)

    # ---- Universo (ficheros .xlsx de RankedStocks y HelloStocks elegidos por el usuario) ------
    @app.get("/universe", response_class=HTMLResponse)
    async def universe(request: Request, src: str = ALL, message: str = "", view: str = ""):
        if (back := recall(request, "universe", ignore=("message", "debug", "view"))) is not None:
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
        exempt_entries = service.quality_exempt()
        spread_only = service.spread_only_tickers()      # solo en fuentes aptas para spreads: liquidez y precio en vez de calidad de la empresa
        sucfg = service.settings.spread_universe
        shown = []
        for row in rows:
            info = replace(qinfos.get(row.ticker) or TickerInfo(row.ticker),
                           sector=_sector_of(row, sector_col, sectors) or ident.get(row.ticker, ("", ""))[1] or None)
            if row.ticker in spread_only:
                if spread_universe_reject(info, sucfg.min_option_liquidity, sucfg.price_min, sucfg.price_max) is None:
                    shown.append(row)
                continue
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
            info = describe_manual(current.name) if current.name in service.manual_sources \
                else describe(current.name, current.criteria)
        identity_cols = {"company", "empresa", "sector"} | set(SYMBOL_HEADERS)   # van delante: no se repiten al final
        skip = {i for i, c in enumerate(table.columns) if _plain(c.name) in identity_cols} if table else set()
        return render(request, "universe.html", no_autorefresh=True, table=table, rows=rows, message=message, groups=message_groups(message),
                      ident=ident, skip=skip,
                      qp=request.query_params, src=current.name if current else ALL, sources=sources, files=files,
                      numbers=numbers, member={t: ", ".join(map(str, n)) for t, n in member.items()},
                      all_count=len(member), manual=[(n, len(t)) for n, t in service.manual_sources.items()],
                      source_info=info, in_watchlist=set(service.watchlist.list()),
                      qinfos=qinfos, with_quality=with_quality, edgar=bool(service.settings.edgar.contact.strip()),
                      exempt_tickers=exempt_tickers, exempt_names=", ".join(qcfg.exempt_sectors),
                      spread_only=spread_only, spread_sources=service.spread_source_names(),
                      spread_cfg=sucfg, spread_builtin=service.settings.tortuga.source_name,
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

    @app.post("/universe/source/spread")
    async def universe_source_spread(request: Request):
        """Marca (`on=1`) o desmarca una fuente como apta para spreads."""
        form = await request.form()
        source, on = str(form.get("src", "")), str(form.get("on", "")) == "1"
        try:
            service.set_spread_source(source, on)
        except WatchlistError as exc:
            return RedirectResponse(f"/universe?{urlencode({'message': _grp('err', 'Error', [], str(exc))})}", status_code=303)
        remembered.pop("universe", None)
        what = "apta para spreads" if on else "ya no es apta para spreads"
        msg = _grp("ok", f"{source}: {what}", [], "")
        return RedirectResponse(f"/universe?{urlencode({'message': msg, 'src': source})}", status_code=303)

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
        form = await request.form()
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

    @app.post("/universe/apply")
    async def universe_apply(request: Request):
        """Añade a la watchlist (o la sustituye por) los tickers marcados."""
        form = await request.form()
        mode = str(form.get("mode", "add"))
        msg = await apply_watchlist(parse_tokens([str(t) for t in form.getlist("sel")]), mode)
        return RedirectResponse(f"/watchlist?{urlencode({'message': msg})}", status_code=303)

    # ---- scanner y simulador ---------------------------------------------------------------
    def parse_scan(qp, set_name: str = "put") -> dict:
        """Lee UN juego de filtros del formulario del scanner (`put` o `spread`; GET o POST). Devuelve form, criteria, error y avisos."""
        submitted = "submitted" in qp
        cand = service.settings.scanner.candidates
        # el descuento máximo no es editable: es siempre el máximo del rango guardado (scanner.candidates)
        base = service.criteria().with_filters(strike_below_pct_max=cand.strike_below_pct_max)
        form = {
            "discount": _fmt(base.strike_below_pct_min),
            "dte_min": str(base.dte_min), "dte_max": str(base.dte_max),
            "min_yield": _fmt(base.min_yield_pct),
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
            **{k: getattr(base, k) for k in MA_SLOPES},
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

        st = service.settings.scanner.strategy       # delta y cercanía a un nivel: marcar el filtro lo aplica
        form.update(iv_any=False, use_delta=False, delta_min=_fmt(st.min_abs_delta) if st.min_abs_delta is not None else "", delta_max=_fmt(st.max_abs_delta),
                    signals=str(base.signals_required),
                    use_near=False, near_pct=_fmt(st.near_level_pct),
                    use_rsi=False, rsi_max=_fmt(st.max_rsi_level), use_boll=False, boll_pctb=_fmt(st.max_bollinger_pctb),
                    use_bw=False, boll_width=_fmt(st.min_bollinger_width_pct),
                    use_atr=False, atr_mult=_fmt(st.min_atr_multiple),
                    use_exp=False, exp_mult=_fmt(st.expected_move_mult),
                    use_ivhv=False, ivhv=_fmt(st.min_iv_hv_ratio), use_ivchg=False, ivchg=_fmt(st.max_iv_change_5d),)

        sp = service.settings.scanner.spreads      # modo spread: marcar cada filtro lo aplica; las cajas traen los valores preconfigurados
        form.update(mode="put", set=set_name, best_width=True,
                    use_rr=True, rr=_fmt(sp.min_return_on_risk_pct), use_mc=True, mc=_fmt(sp.min_credit_usd),
                    use_cs=True, cs=_fmt(sp.max_combo_spread_pct), use_lo=True, lo=_fmt(sp.min_long_oi), use_ls=True, ls=_fmt(sp.max_long_spread_pct),
                    use_mn=True, mn=_fmt(sp.max_loss_nlv_pct), use_ns=True, ns=_fmt(sp.max_naked_strike))

        criteria, error, warnings = None, None, []
        try:
            overrides: dict = {}
            if submitted:
                form["mode"] = qp.get("mode", "put")
                if form["mode"] not in ("put", "spread", "both"):
                    raise ValueError("modo desconocido")
                if any(key in qp for key in ("rr", "mc", "mn", "ss", "cs", "lo", "ls", "ns")):   # bloque visible: lo enviado manda
                    form["best_width"] = "best_width" in qp
                # las cajas de texto viajan siempre que el bloque se ve; si no hay ninguna (p. ej. se acaba de cambiar
                # de instrumento) los límites conservan sus valores marcados por defecto
                for shown_keys in (("rr", "mc", "ss", "cs", "lo", "ls", "mn"), ("ns",)):
                    if any(key in qp for key in shown_keys):
                        for key in shown_keys:
                            form[f"use_{key}"] = f"use_{key}" in qp
                for key in ("rr", "mc", "mn", "ss", "cs", "lo", "ls", "ns"):
                    if key in qp:
                        form[key] = qp.get(key, "").strip()
                for key in ("discount", "dte_min", "dte_max", "min_yield"):
                    form[key] = qp.get(key, "").strip()
                form["ref"] = qp.get("ref", form["ref"])
                form["ref_x"] = qp.get("ref_x", form["ref_x"]).strip()   # ausente = valor de la configuración
                for key, field_name, allowed in (
                    ("trend_dir", "trend_direction", ("off", "up", "down")),
                    ("trend_method", "trend_method", ("low", "swings", "lows")),
                    *((k, k, ("any", "above", "below")) for k in MA_LINES),
                    ("ma_frame", "ma_frame", ("daily", "weekly", "monthly")),
                    *((k, k, ("any", "gte", "lte")) for k in MA_CROSSES),
                    *((k, k, ("any", "up", "down")) for k in MA_SLOPES),
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
                # las medias que no se pueden calcular con esas velas no se ofrecen: se descarta su valor
                for key in unavailable_ma_fields(form["ma_frame"], service.settings.trend.history_days, tcfg.ma_slope_candles):
                    form[key] = "any"
                    overrides[key] = "any"
                form["support"] = "support" in qp
                overrides["require_support"] = form["support"]
                for key, field_name, label in (("price_min", "min_price", "Precio mín."), ("price_max", "max_price", "Precio máx.")):
                    form[key] = qp.get(key, "").strip()
                    overrides[field_name] = _required(form[key], float, label) if form[key] else None
                    if overrides[field_name] is not None and overrides[field_name] < 0:
                        raise ValueError(f"{label} no puede ser negativo")
                if None not in (overrides["min_price"], overrides["max_price"]) and overrides["min_price"] > overrides["max_price"]:
                    raise ValueError("el precio mínimo no puede superar el máximo")
                read_quality(qp, form, overrides, service.settings.scanner.quality, earnings=True, company=False)
                form["touch"] = qp.get("touch", "").strip()
                overrides["min_days_since_touch"] = _required(form["touch"], int, "Días desde el último toque") if form["touch"] else None
                if overrides["min_days_since_touch"] is not None and overrides["min_days_since_touch"] not in tcfg.touch_min_days_options:
                    raise ValueError("días desde el último toque no permitidos")
                for key in optional:
                    form[f"use_{key}"] = f"use_{key}" in qp
                    # un perfil (o enlace) que no trae la caja la muestra con su valor preconfigurado, desmarcada
                    form[key] = qp.get(key, _fmt(shown[key])).strip()
                for key in ("use_delta", "use_near", "use_rsi", "use_boll", "use_bw", "use_ivhv", "use_ivchg", "use_atr", "use_exp", "iv_any"):
                    form[key] = key in qp
                for key in ("delta_min", "delta_max", "near_pct", "rsi_max", "boll_pctb", "boll_width",
                            "ivhv", "ivchg", "atr_mult", "exp_mult", "signals"):
                    # ausente de la URL (p. ej. la estrategia no los envía si están desactivados) = valor preconfigurado
                    if key in qp or key in ("delta_min", "delta_max", "signals"):
                        form[key] = qp.get(key, "").strip()
                overrides["strike_below_pct_min"] = _required(form["discount"], float, "Descuento mín. del strike")
                overrides["min_yield_pct"] = _required(form["min_yield"], float, "Yield mín. del periodo")
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
                if overrides["min_yield_pct"] < 0:
                    raise ValueError("el yield mínimo no puede ser negativo")
                if overrides["dte_min"] < 0 or overrides["dte_min"] > overrides["dte_max"]:
                    raise ValueError("los DTE deben cumplir 0 ≤ mín. ≤ máx.")
            for key, (field_name, cast, _) in optional.items():
                # sin marcar = filtro desactivado, aunque la configuración tenga un valor
                overrides[field_name] = _required(form[key], cast, field_name) if form[f"use_{key}"] else None
            if form["use_delta"]:   # basta con un máximo (o un mínimo): la caja vacía no se aplica
                lo = _required(form["delta_min"], float, "Delta mín.") if form["delta_min"] else None
                hi = _required(form["delta_max"], float, "Delta máx.") if form["delta_max"] else None
                if lo is None and hi is None:
                    raise ValueError("indica una delta mínima o máxima")
                if not all(0 <= v <= 1 for v in (lo, hi) if v is not None) or (lo is not None and hi is not None and lo > hi):
                    raise ValueError("la delta debe cumplir 0 ≤ mín. ≤ máx. ≤ 1 (en valor absoluto)")
                overrides["min_abs_delta"], overrides["max_abs_delta"] = lo, hi
            if form["signals"] not in ("", "0", "1", "2", "3", "4", "5"):
                raise ValueError("número de señales de entrada no permitido")
            # con menos señales marcadas que las exigidas, el selector pasa al nº de marcadas (0 = «Todas las marcadas», sin señales no hay nada que exigir)
            marked = sum(1 for k in ("use_rsi", "use_boll", "use_near") if form[k])
            form["signals"] = str(min(int(form["signals"] or 0), marked))
            overrides["signals_required"] = int(form["signals"])
            overrides["iv_any"] = bool(form["iv_any"])
            if form["use_near"]:
                overrides["near_level_pct"] = _required(form["near_pct"], float, "Distancia a la media de 50 o al soporte (%)")
                if not (0 < overrides["near_level_pct"] <= 50):
                    raise ValueError("la distancia a la media o al soporte debe estar entre 0 y 50 %")
            if form["use_ivhv"]:
                overrides["min_iv_hv_ratio"] = _required(form["ivhv"], float, "IV / HV30 mínimo")
                if not (0 < overrides["min_iv_hv_ratio"] <= 10):
                    raise ValueError("IV / HV30 debe estar entre 0 y 10")
            if form["use_ivchg"]:
                overrides["max_iv_change_5d"] = _required(form["ivchg"], float, "Cambio de IV a 5 días")
            if form["use_exp"]:
                overrides["expected_move_mult"] = _required(form["exp_mult"], float, "Movimiento esperado (×)")
                if not (0 < overrides["expected_move_mult"] <= 5):
                    raise ValueError("el múltiplo del movimiento esperado debe estar entre 0 y 5")
            if form["use_atr"]:
                overrides["min_atr_multiple"] = _required(form["atr_mult"], float, "Distancia en ATR")
                if not (0 < overrides["min_atr_multiple"] <= 10):
                    raise ValueError("la distancia en ATR debe estar entre 0 y 10")
            if form["use_rsi"]:
                overrides["rsi_rebound_max"] = _required(form["rsi_max"], float, "Nivel del RSI")
                if not (0 < overrides["rsi_rebound_max"] < 100):
                    raise ValueError("el nivel del RSI debe estar entre 0 y 100")
            if form["use_boll"]:
                overrides["max_bollinger_pctb"] = _required(form["boll_pctb"], float, "%B de Bollinger")
                if not (-1 <= overrides["max_bollinger_pctb"] <= 2):
                    raise ValueError("el %B de Bollinger debe estar entre −1 y 2")
            if form["use_bw"]:
                overrides["min_bollinger_width_pct"] = _required(form["boll_width"], float, "Ancho de bandas (%)")
                if not (0 < overrides["min_bollinger_width_pct"] <= 100):
                    raise ValueError("el ancho de las bandas debe estar entre 0 y 100 %")
            if set_name == "spread":     # los filtros propios del spread solo existen en su juego de filtros
                overrides.update(spread_mode=True, spread_widths=tuple(sorted(set(sp.width_options))), best_width=bool(form["best_width"]))
                for use, key, field_name, label, upper in (
                        ("use_rr", "rr", "min_return_on_risk_pct", "Retorno s/ riesgo mín. (%)", None),
                        ("use_mc", "mc", "min_spread_credit", "Crédito mín. ($ por contrato)", None),
                        ("use_lo", "lo", "min_long_oi", "OI mín. de la pata comprada", None),
                        ("use_cs", "cs", "max_combo_spread_pct", "Spread máx. del combo (% del crédito)", None),
                        ("use_ls", "ls", "max_long_spread_pct", "Spread máx. de la pata comprada (%)", None),
                        ("use_mn", "mn", "max_loss_nlv_pct", "Pérdida máxima (% del NLV)", 100)):
                    if form[use]:
                        overrides[field_name] = _required(form[key], float, label)
                        if overrides[field_name] <= 0 or (upper is not None and overrides[field_name] >= upper):
                            raise ValueError(f"{label} fuera de rango")
                if form["mode"] == "both":
                    if not form["use_ns"]:
                        raise ValueError("en el modo «Ambos» marca el strike máximo de la put desnuda")
                    overrides["max_naked_strike"] = _required(form["ns"], float, "Strike máximo de la put desnuda ($)")
                    if overrides["max_naked_strike"] <= 0:
                        raise ValueError("Strike máximo de la put desnuda ($) fuera de rango")
                    overrides["mixed"] = True
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

    def parse_pair(qp) -> dict:
        """Lee los dos juegos de filtros (put y spread): {shown, put, spread, other_params, mode}; cada uno es el resultado de `parse_scan`."""
        shown, own, other = split_sets(qp)
        struct = {k: v for k, v in dict(qp).items() if k in STRUCT_KEYS}
        sets = {shown: own, ("put" if shown == "spread" else "spread"): other}
        parsed = {name: parse_scan({**struct, **params}, name) for name, params in sets.items()}
        return dict(shown=shown, put=parsed["put"], spread=parsed["spread"], other_params=other,
                    mode=parsed[shown]["form"]["mode"])

    def get_profiles() -> tuple[list[Profile], str]:
        """Perfiles guardados desde la aplicación o, mientras no haya, los que salen de los presets y la estrategia."""
        profiles, default = service.stored_profiles() or seed_profiles(service.settings)
        if not any(p.name == FALLBACK_PROFILE for p in profiles):     # el perfil de las puts que no encajan por DTE siempre existe
            profiles = [*profiles, seed_fallback(service.settings)]
        return profiles, default

    service.profile_provider = get_profiles

    @app.get("/scanner/todos", response_class=HTMLResponse)
    async def scanner_all(request: Request):
        """Un solo escaneo con los filtros de varios perfiles (`pf` repetido; sin ninguno, todos menos el de las puts que no encajan): cada
        put o spread que cumple los de alguno sale una vez, con la columna de los perfiles que cumple. Los filtros son los guardados en cada perfil."""
        profiles, _ = get_profiles()
        candidates = [p for p in profiles if p.name != FALLBACK_PROFILE]
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
        return render(request, "scanner_all.html", rows=rows, counts=counts, candidates=candidates, chosen={p.name for p in chosen}, errors=errors,
                      colors={p.name: p.color for p in profiles}, nlv_usd=service.nlv_usd(), community=service.community_counts(),
                      owned=service.owned_options(), techs=service.tech_summaries(ScanOutput([m.short for m in rows], 0, {}, [], rows)),
                      watch_data=True)

    @app.post("/scanner/profile/save")
    async def scanner_profile_save(request: Request):
        """Guarda los filtros actuales del formulario en un perfil (existente o nuevo): el juego del instrumento que se está viendo."""
        form = await request.form()
        name = str(form.get("name", "")).strip()
        qp = dict(parse_qsl(str(form.get("query", "")), keep_blank_values=True))
        mode = qp.get("mode", "put") if qp.get("mode") in ("put", "spread", "both") else "put"
        shown, own, other = split_sets(qp)
        put_params, spread_params = (own, other) if shown == "put" else (other, own)
        profiles, default = get_profiles()
        try:
            existing = next((p for p in profiles if p.name.casefold() == name.casefold()), None)
            if existing is not None:
                saved = existing.with_sets(put_params, spread_params)
                profiles = [saved if p is existing else p for p in profiles]
            else:
                instruments = ("put",) if mode == "put" else ("spread",) if mode == "spread" else ("put", "spread")
                saved = Profile(name, instruments).with_sets(put_params, spread_params)
                profiles = [*profiles, saved]
            service.save_profiles(profiles, default)
        except ValueError:
            return RedirectResponse("/scanner?" + urlencode({**qp, "submitted": "1"}), status_code=303)
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner?" + profile_link(saved, mode, shown), status_code=303)

    @app.post("/scanner/profiles")
    async def scanner_profiles_save(request: Request):
        """Nombre, color, orden, instrumentos activos, gestión, duplicado, borrado y perfil inicial de los perfiles (los filtros se guardan desde el Scanner)."""
        form = await request.form()
        profiles, default = get_profiles()
        by_name = {p.name: p for p in profiles}
        rows: list[tuple[float, int, Profile]] = []      # (orden elegido, posición, perfil)
        chosen = str(form.get("default", ""))
        new_default = default
        for i in range(int(form.get("n_profiles", 0) or 0)):
            orig = by_name.get(str(form.get(f"pf{i}_orig", "")))
            if orig is None or (form.get(f"pf{i}_del") and orig.name != FALLBACK_PROFILE):
                continue
            instruments = tuple(k for k in ("put", "spread") if form.get(f"pf{i}_{k}"))
            name = orig.name if orig.name == FALLBACK_PROFILE else str(form.get(f"pf{i}_name", ""))     # «Tortuga» no se renombra ni se borra
            color = str(form.get(f"pf{i}_color", orig.color)) if form.get(f"pf{i}_usecolor") else ""
            kept = Profile(name, instruments, orig.put, orig.spread, str(form.get(f"pf{i}_mgmt", orig.management)), color)
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
        except ValueError as exc:
            return RedirectResponse("/scanner/config?" + urlencode({"error": str(exc)}), status_code=303)
        remembered.pop("scanner", None)
        return RedirectResponse("/scanner/config?" + urlencode({"ok": "Perfiles guardados."}), status_code=303)

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
        return render(request, "scanner_config.html", profiles=profiles, default_profile=default_profile, fallback=FALLBACK_PROFILE,
                      profiles_saved=service.stored_profiles() is not None, vix=vix, ok=ok, error=error,
                      gap=gap, user_edited=bool(service.meta.get(SCANNER_USER_CONFIG)),
                      ranges={p.name: dte_range(p) for p in profiles})

    @app.post("/scanner/config")
    async def scanner_config_save(request: Request):
        """Aviso del VIX del Panel (lo único de la estrategia que se edita aquí; los filtros viven en los perfiles)."""
        form = await request.form()
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
        pair = parse_pair(request.query_params)
        parsed = pair[pair["shown"]]       # se mide el juego de filtros que se está viendo
        if parsed["criteria"] is None:
            return render(request, "_impact.html", error=parsed["error"] or "Parámetros no válidos", report=None)
        return render(request, "_impact.html", error=None, report=service.scan_impact(parsed["criteria"]))

    @app.get("/scanner/spread-check", response_class=HTMLResponse)
    async def scanner_spread_check(request: Request):
        """Panel «Revisión de spreads» (fragmento HTML, se carga al abrirlo): cada put vendida que pasa como put sola y sus condiciones de spread."""
        parsed = parse_pair(request.query_params)["spread"]       # la revisión parte de los filtros del juego de spread
        c = parsed["criteria"]
        if c is None or not c.spread_mode or request.query_params.get("mode", "put") == "put":
            return render(request, "_spread_check.html", error=parsed["error"] or "Solo en los modos Spread y Ambos.", rows=[], total=0, dropped=0, columns=[], mixed=False)
        every = service.spread_check(c)
        rows = [r for r in every if r.outcome == "no"]      # solo las que se descartan; las que salen ya están en la tabla de resultados
        return render(request, "_spread_check.html", error=None, rows=rows[:SPREAD_CHECK_MAX], total=len(every), dropped=len(rows),
                      columns=SPREAD_CHECK_COLUMNS, mixed=c.mixed)

    @app.get("/scanner", response_class=HTMLResponse)
    async def scanner(request: Request, debug: int = 0):
        if (back := recall(request, "scanner")) is not None:
            return back
        profiles, default_profile = get_profiles()
        if "submitted" not in request.query_params and "reset" not in request.query_params:    # sin consulta: se carga el perfil inicial
            first = next((p for p in profiles if p.name == default_profile), profiles[0])
            return RedirectResponse("/scanner?" + profile_link(first), status_code=303)
        pair = parse_pair(request.query_params)
        mode, shown = pair["mode"], pair["shown"]
        parsed = dict(pair[shown])
        # el escaneo usa el juego de put, el de spread o los dos según el instrumento; un error del juego visible se ve siempre
        used = {"put": ("put",), "spread": ("spread",), "both": ("put", "spread")}.get(mode, ("put",))
        bad = next((pair[n] for n in (shown, *used) if pair[n]["criteria"] is None), None)
        out = c = None
        if bad is None:
            c = pair["put"]["criteria"] if mode == "put" else pair["spread"]["criteria"]
            if mode == "both":
                out = service.scan_both(pair["put"]["criteria"], pair["spread"]["criteria"], include_rejections=bool(debug))
            else:
                out = service.scan(c, include_rejections=bool(debug))
            parsed["criteria"] = c
        else:
            parsed["criteria"], parsed["error"] = None, bad["error"]
        if c is not None and any(pair[n]["criteria"].technical_active for n in used):
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
        active_name = request.query_params.get("profile", "")
        default_qs = profile_link(next((p for p in profiles if p.name == default_profile), profiles[0]))
        profile_buttons = [dict(name=p.name, qs=profile_link(p), on=p.name == active_name, instruments=p.instruments, color=p.color,
                                strategy=p.put.get("use_delta") == "on") for p in profiles]
        active_profile = next((p for p in profiles if p.name == active_name), None)
        qcfg = service.settings.scanner.quality
        counts = quality_counts(service.ticker_info.all(), service.watchlist.list(), service.criteria(), qcfg)
        strategy_qs, strategy_on = strategy_link(service.settings, parsed["form"], service.criteria(), request.query_params)
        yield_max = max((r.yield_ref_annualized_pct or 0.0 for r in out.results), default=0.0) if out else 0.0   # escala de las barras del yield
        return render(request, "scanner.html", no_autorefresh=True, out=out, other_params=pair["other_params"], owned=service.owned_options(), ref_label=ref_label, watch_data=True, presets=presets, profile_buttons=profile_buttons, active_profile=active_profile, default_qs=default_qs,
                      community=service.community_counts(), techs=service.tech_summaries(out) if out else {},
                      yield_max=yield_max, nlv_usd=service.nlv_usd(), strategy_qs=strategy_qs, strategy_on=strategy_on,
                      count_text=lambda field_name, value: _count_text(counts, (field_name, value)),
                      candidates=service.settings.scanner.candidates,
                      margin=service.settings.scanner.catalog_margin_pct,
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
    async def strike_chart(ticker: str, strike: float):
        """Gráfico de cierres mensuales del ticker frente al strike, con los días desde el último toque
        (fragmento HTML que el Scanner muestra en una ventana)."""
        closes = service.bars.closes(ticker)
        if not closes or strike <= 0:
            return HTMLResponse('<p class="muted">Sin histórico de cierres de este ticker: se descarga con la actualización diaria.</p>')
        tcfg = service.settings.scanner.technical
        history = strike_history(sorted(closes.items()), strike, service.now().date(), tcfg.chart_months)
        alerts = service.filing_alert_map().get(norm_ticker(ticker), [])
        box = ('<div class="banner warn"><b>⚠ Presentaciones recientes ante la SEC</b><ul style="margin:4px 0 0 18px;padding:0">'
               + "".join(f"<li>{escape(a.text)}</li>" for a in alerts) + "</ul></div>") if alerts else ""
        return HTMLResponse(box + strike_chart_html(history, ticker, strike, tcfg.chart_near_pct))

    @app.get("/chart/mini.svg")
    async def strike_mini(ticker: str, strike: float):
        """Miniatura del gráfico del strike para las filas del Scanner (se pide al hacerse visible la fila)."""
        tcfg = service.settings.scanner.technical
        closes = service.bars.closes(ticker)
        history = strike_history(sorted(closes.items()), strike, service.now().date(), tcfg.chart_months)
        return Response(strike_mini_svg(history, strike, tcfg.chart_near_pct), media_type="image/svg+xml",
                        headers={"Cache-Control": "max-age=300"})

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
