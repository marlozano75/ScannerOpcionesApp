"""Formularios del Scanner y del Universo: filtros de calidad, valores de la estrategia y de los perfiles, enlaces de perfil y lectura de la configuración.

Funciones y constantes sin estado (reciben los ajustes por argumento); `web.py` las usa dentro de `create_app` y las vuelve a exportar."""
from __future__ import annotations

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.profiles import Profile
from scanner_opciones.scanner.quality import ticker_quality_reject
from typing import Optional
from urllib.parse import parse_qsl, urlencode


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
    from scanner_opciones.scanner.quality import exemption, ticker_quality_reject

    exempt = qcfg.exempt_entries      # una propiedad que arma la tupla: fuera del bucle por ticker
    fichas = [infos.get(t) or TickerInfo(t) for t in tickers]
    flags = [exemption(info, exempt) for info in fichas]       # la exención no depende del filtro: una vez por ticker, no por opción

    def passing(**overrides) -> int:
        c = base.with_filters(**overrides)
        return sum(1 for info, f in zip(fichas, flags) if ticker_quality_reject(info, c, exempt, f) is None)

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
    "rsi_max", "use_boll", "boll_pctb", "use_bw", "boll_width", "ma200", "q_earn", "q_liq", "use_credit", "credit", "reset", "debug"))


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



VIX_FIELDS = (    # aviso del VIX del Panel (`scanner.strategy`): única parte de la estrategia que se edita en /scanner/config
    ("min_vix", "Primas pobres si VIX <", "float"), ("ideal_vix_min", "Ideal desde", "float"),
    ("max_vix", "Nervioso si VIX >", "float"),
)

SPREAD_FIELDS = (    # valores iniciales del modo Spread del Scanner (`scanner.spreads`); en el formulario van como `sp_<clave>`
    ("width_options", "Anchos que se prueban ($, separados por comas)", "floats"),
    ("min_return_on_risk_pct", "Retorno mín. sobre riesgo (%, hasta el vencimiento)", "float"),
    ("min_credit_usd", "Crédito mín. ($ por contrato)", "float"),
    ("max_combo_spread_pct", "Spread bid/ask máx. del combo (% del crédito)", "float"),
    ("min_long_oi", "OI mín. de la pata comprada", "int"),
    ("max_long_spread_pct", "Spread bid/ask máx. de la pata comprada (%)", "float"),
    ("max_loss_nlv_pct", "Pérdida máxima (% del NLV)", "float"),
    ("max_long_abs_delta", "Sosnoff: |delta| máx. de la put comprada", "float"),
    ("min_credit_width_pct", "Sosnoff: crédito / ancho mín. (%)", "float"),
    ("max_credit_width_pct", "Sosnoff: crédito / ancho máx. (%, vacío = sin tope)", "ofloat"),
    ("fixed_widths", "Sosnoff: anchos fijos ($, separados por comas)", "floats"),
    ("sosnoff_delta_min", "Sosnoff: |delta| mín. de la put vendida (botón «Aplicar»)", "float"),
    ("sosnoff_delta_max", "Sosnoff: |delta| máx. de la put vendida (botón «Aplicar»)", "float"),
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
    if st.min_credit_usd is not None:
        params["use_credit"], params["credit"] = "on", _fmt(st.min_credit_usd)
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
    if preset.min_credit_usd is not None:
        params["use_credit"], params["credit"] = "on", _fmt(preset.min_credit_usd)
    on = all(form.get(k) == params[k] for k in ("discount", "dte_min", "dte_max", "min_yield"))
    kept = {k: v for k, v in (current.items() if current is not None else ()) if k not in STRATEGY_KEYS} if current is not None and "submitted" in current else {}
    return urlencode({**kept, **params}), on


SEED_COLORS = {"Mensual (TPC)": "#2e7d32", "Táctico (TPC)": "#e08a00", "TastyPut": "#1f6feb", "TastySpread": "#0b8a8f"}
UNASSIGNED_COLOR = "#7b5ea7"      # etiqueta «Sin perfil» del Panel


def seed_profiles(settings) -> tuple[list[Profile], str]:
    """Perfiles que salen de los presets TPC y de la estrategia Tastytrade (config.yaml o editados en /scanner/config) mientras no se
    guarden perfiles propios. TPC solo con put vendida; la estrategia Tastytrade se reparte en TastyPut (put vendida) y TastySpread
    (spread de puts). El inicial es TastyPut. Las puts se gestionan con TPC (mantener hasta el vencimiento) y los spreads con Tastytrade."""
    out = []
    for p in settings.scanner.presets:
        params = dict(parse_qsl(preset_link(settings, p, {})[0]))
        out.append(Profile(p.name, ("put",), management="tpc", color=SEED_COLORS.get(p.name, "")).with_filters("put", params))
    params = dict(parse_qsl(strategy_link(settings, {}, None)[0]))
    out.append(Profile("TastyPut", ("put",), management="tpc", color=SEED_COLORS["TastyPut"]).with_filters("put", params))
    out.append(Profile("TastySpread", ("spread",), color=SEED_COLORS["TastySpread"]).with_filters("spread", params))
    return out, "TastyPut"


def profile_link(p: Profile) -> str:
    """Consulta que carga el perfil en el Scanner (reemplaza todo el formulario): los filtros de su instrumento."""
    return urlencode({**p.set_params(p.mode), "submitted": "1", "mode": p.mode, "profile": p.name})


# parámetros de la URL que no pertenecen a ningún juego de filtros
STRUCT_KEYS = frozenset(("submitted", "mode", "profile", "set", "debug", "reset", "message"))      # `set` solo en enlaces antiguos


def split_sets(qp) -> tuple[str, dict]:
    """(instrumento, parámetros de sus filtros). El instrumento es `mode` (un enlace antiguo con `mode=both` cuenta como put); los `o_...`
    de los enlaces antiguos (el juego del otro instrumento) se ignoran."""
    items = dict(qp)
    shown = "spread" if items.get("mode") == "spread" else "put"
    return shown, {k: v for k, v in items.items() if k not in STRUCT_KEYS and not k.startswith("o_")}


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
