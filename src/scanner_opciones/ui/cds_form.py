"""Formulario del perfil «Call Debit Spread» (MTO CDS): de los parámetros de la URL (o de un perfil guardado) a un `CdsSettings`.

La ventana de lo que SE GUARDA y se cotiza (DTE, % OTM de la comprada y anchos) es la de `scanner.cds` en config.yaml, igual que
`scanner.candidates` para las puts: el formulario solo puede ESTRECHARLA (un valor fuera se recorta y se avisa; un ancho que el catálogo no
guarda se descarta). Débito, OI, spread de las patas, movimiento mínimo esperado, retorno máximo y SMA200 no afectan al catálogo y son libres."""
from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.scanner import cds_profile
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, unavailable_ma_fields
from scanner_opciones.rankedstocks.loader import clean_ticker
from scanner_opciones.ui.scanner_forms import _fmt, quality_form, read_quality

# claves de la URL que identifican la página y no son filtros del perfil
STRUCT = frozenset(("submitted", "mode", "profile", "reset", "message"))
# filtros de la empresa y del ticker (no del spread): del mismo formulario y con el mismo parser que el Scanner de puts. El filtro de resultados no
# existe aquí: con DTE 200–500 siempre hay unos resultados antes del vencimiento y descartaría todo.
TICKER_KEYS = ("q_profit", "q_quarters", "q_fcf", "q_lev", "q_liq", "price_min", "price_max")


def _num(text: str, name: str, kind=float, allow_empty: bool = False):
    text = str(text).strip().replace(",", ".")
    if not text:
        if allow_empty:
            return None
        raise ValueError(f"{name}: falta el valor")
    try:
        return kind(float(text)) if kind is int else float(text)
    except ValueError:
        raise ValueError(f"{name}: «{text}» no es un número") from None


def default_form(service) -> dict:
    """Valores iniciales del formulario: los de `scanner.cds` (config.yaml) y el precio de venta de referencia del Scanner."""
    return cds_profile.default_form(service.settings)


def with_allw(params: dict) -> dict:
    """Parámetros de un perfil guardado antes de la casilla «Todos los anchos»: se leen con ella marcada (el valor por defecto)."""
    return params if "allw_set" in params else {**params, "allw": "on", "allw_set": "on"}


def parse_cds(service, qp) -> dict:
    """Lee el formulario CDS. Devuelve `form` (valores para pintar), `cds` (`CdsSettings` o None si hay error), `ref` (precio de venta de referencia:
    `price_reference`, `price_spread_pct`), `notices` (valores recortados a la ventana guardada) y `error`."""
    catalog = service.settings.scanner.cds
    form = default_form(service)
    submitted = "submitted" in qp
    notices: list[str] = []
    cds: Optional[CdsSettings] = None
    ref: dict = {}
    ticker_filters: dict = {}
    error: Optional[str] = None
    if submitted:
        for key in ("dte_min", "dte_max", "otm_min", "otm_max", "widths", "wpct_min", "wpct_max", "dmin", "dmax", "oi", "oi_s", "ls", "ls_s", "lvol", "svol", "bsz", "asz", "gain", "days", "mult",
                    "ret", "prob", "nd", "xp", "top", "sma", "ref", "ref_x"):
            if key in qp:
                form[key] = str(qp.get(key, "")).strip()
        for new, old in (("oi_s", "oi"), ("ls_s", "ls")):        # perfil guardado antes de separar las patas: el valor único valía para las dos
            if new not in qp and old in qp:
                form[new] = form[old]
        for key in ("best", "use_ret", "use_prob", "use_nd", "use_xp", "use_sma", "use_earn", "use_bsz", "use_asz", "use_lvol", "use_svol"):          # casillas: ausentes = desmarcadas (solo si el formulario se envió entero)
            form[key] = key in qp
        form["allw"] = "allw" in qp or "allw_set" not in qp      # `allw_set` marca que el formulario trae la casilla: sin él (perfil o enlace anterior a la casilla) vale «marcada»
        form["allw_set"] = True
    try:
        dte_min = max(_num(form["dte_min"], "DTE mínimo", int), catalog.dte_min)
        dte_max = min(_num(form["dte_max"], "DTE máximo", int), catalog.dte_max)
        otm_min = max(_num(form["otm_min"], "% OTM mínimo"), catalog.long_otm_pct_min)
        otm_max = min(_num(form["otm_max"], "% OTM máximo"), catalog.long_otm_pct_max)
        if _num(form["dte_min"], "DTE mínimo", int) < catalog.dte_min or _num(form["dte_max"], "DTE máximo", int) > catalog.dte_max:
            notices.append(f"DTE recortado a {catalog.dte_min}–{catalog.dte_max}: es la ventana que guarda y cotiza scanner.cds")
        if _num(form["otm_min"], "% OTM mínimo") < catalog.long_otm_pct_min or _num(form["otm_max"], "% OTM máximo") > catalog.long_otm_pct_max:
            notices.append(f"% OTM recortado a {_fmt(catalog.long_otm_pct_min)}–{_fmt(catalog.long_otm_pct_max)}: es la ventana que guarda scanner.cds")
        wanted = [_num(w, "Ancho") for w in str(form["widths"]).replace(";", ",").split(",") if w.strip()]
        known = {round(w, 4) for w in catalog.width_options}
        widths = tuple(w for w in wanted if round(w, 4) in known)
        dropped = [w for w in wanted if round(w, 4) not in known]
        if dropped:
            notices.append("Anchos que el catálogo no guarda y se ignoran: " + ", ".join(_fmt(w) for w in dropped)
                           + " (añádelos a scanner.cds.width_options y haz una actualización diaria)")
        if not widths and not form["allw"]:
            raise ValueError("Elige al menos un ancho de los que guarda el catálogo: " + ", ".join(_fmt(w) for w in catalog.width_options))
        cds = catalog.model_copy(update=dict(
            dte_min=dte_min, dte_max=dte_max, long_otm_pct_min=otm_min, long_otm_pct_max=otm_max, width_options=widths,
            width_pct_min=_num(form["wpct_min"], "Ancho mínimo (%)", allow_empty=True), width_pct_max=_num(form["wpct_max"], "Ancho máximo (%)", allow_empty=True),
            best_width=bool(form["best"]), all_widths=bool(form["allw"]),
            min_debit_usd=_num(form["dmin"], "Débito mínimo"), max_debit_usd=_num(form["dmax"], "Débito máximo"),
            min_long_oi=_num(form["oi"], "OI mínimo de la comprada", int), min_short_oi=_num(form["oi_s"], "OI mínimo de la vendida", int),
            max_long_spread_pct=_num(form["ls"], "Spread máximo de la comprada"), max_short_spread_pct=_num(form["ls_s"], "Spread máximo de la vendida"),
            min_long_volume=_num(form["lvol"], "Volumen mínimo de la comprada", int) if form["use_lvol"] else None,
            min_short_volume=_num(form["svol"], "Volumen mínimo de la vendida", int) if form["use_svol"] else None,
            min_bid_size=_num(form["bsz"], "Bid size mínimo", int) if form["use_bsz"] else None,
            min_ask_size=_num(form["asz"], "Ask size mínimo", int) if form["use_asz"] else None,
            target_gain_pct=_num(form["gain"], "Objetivo de ganancia"), move_days=_num(form["days"], "Días del movimiento", int),
            max_required_move_mult=_num(form["mult"], "Múltiplo del movimiento esperado"),
            min_prob_pct=_num(form["prob"], "Probabilidad mínima") if form["use_prob"] else None,
            min_net_delta=_num(form["nd"], "Delta neta mínima") if form["use_nd"] else None,
            min_exit_profit_usd=_num(form["xp"], "Ganancia de salida mínima") if form["use_xp"] else None,
            exclude_earnings=bool(form["use_earn"]),
            top_per_ticker=_num(form["top"], "Máximo por ticker", int),
            min_max_return_pct=_num(form["ret"], "Retorno máximo mínimo") if form["use_ret"] else None,
            sma200_below_pct_min=_num(form["sma"], "SMA200") if form["use_sma"] else None))
        CdsSettings.model_validate(cds.model_dump())           # los mismos controles que la configuración
        if form["ref"] not in {r.value for r in PriceReference}:
            raise ValueError("precio de venta de referencia desconocido")
        ref = dict(price_reference=PriceReference(form["ref"]), price_spread_pct=_num(form["ref_x"], "X del precio de referencia"))
        ticker_filters = _read_ticker_filters(service, qp, form, submitted)
        ref.update(ticker_filters)
        ref.update(_read_technical(service, qp, form))
    except (ValueError, ValidationError) as exc:
        error = str(exc).replace("\n", " ")
        cds = None
    return dict(form=form, cds=cds, ref=ref, notices=notices, error=error, submitted=submitted)


def _read_technical(service, qp, form: dict) -> dict:
    """Técnicos de entrada (señales: cercanía a la MA50/soporte, RSI, Bollinger) y medias móviles / EMA: como en el Scanner de puts. Devuelve los campos del
    criterio (`ScanCriteria`) que fijan; todos van siempre, así los valores de la configuración de las puts no se cuelan en el CDS."""
    tcfg = service.settings.scanner.technical
    out: dict = dict(trend_direction="off", require_support=False, min_days_since_touch=None, near_level_pct=None, rsi_rebound_max=None,
                     max_bollinger_pctb=None, min_bollinger_width_pct=None, signals_required=0)
    for key in ("use_near", "use_rsi", "use_boll", "use_bw"):
        form[key] = key in qp
    for key in ("signals", "near_pct", "rsi_max", "boll_pctb", "boll_width"):
        if key in qp:
            form[key] = str(qp.get(key, "")).strip()
    if str(form["signals"]) not in ("", "0", "1", "2", "3", "4", "5"):
        raise ValueError("número de señales de entrada no permitido")
    marked = sum(1 for k in ("use_rsi", "use_boll", "use_near") if form[k])      # con menos señales marcadas que las exigidas, se exigen las marcadas
    form["signals"] = str(min(int(form["signals"] or 0), marked))
    out["signals_required"] = int(form["signals"])
    for use, key, field_name, label, lo, hi in (
            ("use_near", "near_pct", "near_level_pct", "Distancia a la media de 50 o al soporte (%)", 0, 50),
            ("use_rsi", "rsi_max", "rsi_rebound_max", "Nivel del RSI", 0, 100),
            ("use_boll", "boll_pctb", "max_bollinger_pctb", "%B de Bollinger", -1, 2),
            ("use_bw", "boll_width", "min_bollinger_width_pct", "Ancho de bandas (%)", 0, 100)):
        if form[use]:
            out[field_name] = _num(form[key], label)
            below = out[field_name] < lo if lo < 0 else out[field_name] <= lo
            if below or out[field_name] > hi or (field_name == "rsi_rebound_max" and out[field_name] >= 100):
                raise ValueError(f"{label}: fuera de rango")
    for key, allowed in (("ma_frame", ("daily", "weekly", "monthly")), *((k, ("any", "above", "below")) for k in MA_LINES),
                         *((k, ("any", "gte", "lte")) for k in MA_CROSSES), *((k, ("any", "up", "down")) for k in MA_SLOPES)):
        form[key] = qp.get(key, form[key])
        if form[key] not in allowed:
            raise ValueError(f"valor desconocido en «{key}»")
        out[key] = form[key]
    for key in unavailable_ma_fields(form["ma_frame"], service.settings.trend.history_days, tcfg.ma_slope_candles):     # medias que no caben en el histórico
        form[key] = out[key] = "any"
    return out


def _read_ticker_filters(service, qp, form: dict, submitted: bool) -> dict:
    """Calidad de la empresa, liquidez de las opciones, precio del subyacente y fuentes del Universo: se leen como en el Scanner de puts. Devuelve los
    campos del criterio (`ScanCriteria`) que cambian; deja en `form` lo que hay que pintar."""
    qcfg = service.settings.scanner.quality
    out: dict = {}
    if submitted:
        read_quality(qp, form, out, qcfg, earnings=False, liquidity=True, company=True)
        for key, field_name in (("price_min", "min_price"), ("price_max", "max_price")):
            form[key] = str(qp.get(key, "")).strip()
            out[field_name] = _num(form[key], "Precio del subyacente", allow_empty=True)
            if out[field_name] is not None and out[field_name] < 0:
                raise ValueError("el precio del subyacente no puede ser negativo")
        if None not in (out["min_price"], out["max_price"]) and out["min_price"] > out["max_price"]:
            raise ValueError("el precio mínimo no puede superar el máximo")
        universe = service.universe_sources
        legacy = {new: old for old, new in service.legacy_radar_source_names().items()}
        form["fsrc"] = [src.name for src in universe if f"fs_{src.name}" in qp or f"fs_{legacy.get(src.name)}" in qp]
        form["use_src"] = ("use_src" in qp) if "src" in qp else bool(form["fsrc"])
        if form["use_src"]:
            chosen = set(form["fsrc"])
            out["source_names"] = tuple(form["fsrc"])
            out["source_tickers"] = frozenset(clean_ticker(r.ticker) for src in universe if src.name in chosen for r in src.table.rows)
    return out


def form_query(form: dict) -> dict:
    """Parámetros de la URL que reproducen `form` (para guardar en un perfil o recargar)."""
    out = {k: ("on" if v is True else str(v)) for k, v in form.items() if v is not False and k not in ("fsrc", "use_src")}
    if "fsrc" in form:                       # fuentes del Universo: `src=1` marca que el bloque se envió y una casilla `fs_<fuente>` por fuente
        out["src"] = "1"
        if form.get("use_src"):
            out["use_src"] = "on"
        out.update({f"fs_{name}": "on" for name in form["fsrc"]})
    return out
