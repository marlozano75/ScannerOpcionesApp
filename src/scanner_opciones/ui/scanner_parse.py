"""Lectura del formulario del Scanner: de los parámetros de la URL a los valores del formulario y a los `ScanCriteria` (un juego de filtros, el de put o el de spread)."""
from __future__ import annotations

from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.rankedstocks.loader import clean_ticker
from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, unavailable_ma_fields
from scanner_opciones.ui.scanner_forms import _fmt, _required, quality_form, read_quality


def parse_scan(service, qp, set_name: str = "put") -> dict:
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
        "credit": ("min_put_credit", float, base.min_put_credit),
        "risk": ("max_capital_risk_pct", float, base.max_capital_risk_pct),
    }
    form.update(fl_eps=base.floor_eps, fl_q=str(base.floor_quarters), fl_fcf=base.floor_fcf)       # calidad mínima de la put (de cada perfil)
    universe = service.universe_sources
    form["fsrc"] = []       # fuentes del Universo elegidas (casillas `fs_<fuente>`)
    form["use_src"] = False  # casilla «Filtrar por fuentes»: activa y sin ninguna marcada, no sale nada
    fv = service.settings.scanner.filter_values
    shown = {"oi": fv.min_oi, "bidsize": fv.min_bid_size, "spread": fv.max_spread_pct,
             "ivr": fv.min_iv_rank, "ivp": fv.min_iv_percentile, "credit": fv.min_put_credit, "risk": fv.max_capital_risk_pct}
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
    form.update(mode=set_name, set=set_name, best_width=True,
                use_rr=True, rr=_fmt(sp.min_return_on_risk_pct), use_mc=True, mc=_fmt(sp.min_credit_usd),
                use_cs=True, cs=_fmt(sp.max_combo_spread_pct), use_lo=True, lo=_fmt(sp.min_long_oi), use_ls=True, ls=_fmt(sp.max_long_spread_pct),
                use_mn=True, mn=_fmt(sp.max_loss_nlv_pct),
                # criterios de Sosnoff: empiezan desmarcados (se marcan a mano o con el botón «Aplicar criterios de Sosnoff»)
                use_ld=False, ld=_fmt(sp.max_long_abs_delta), use_cw=False, cw=_fmt(sp.min_credit_width_pct),
                cwx=_fmt(sp.max_credit_width_pct) if sp.max_credit_width_pct is not None else "",
                use_wd=False, wd=", ".join(_fmt(w) for w in sp.fixed_widths))

    criteria, error, warnings = None, None, []
    try:
        overrides: dict = {}
        if submitted:
            form["mode"] = qp.get("mode", "put")
            if form["mode"] == "both":      # enlace antiguo del modo «Ambos» (retirado): se ve como put
                form["mode"] = "put"
            if form["mode"] not in ("put", "spread"):
                raise ValueError("modo desconocido")
            if any(key in qp for key in ("rr", "mc", "mn", "ss", "cs", "lo", "ls", "ld", "cw", "wd")):   # bloque visible: lo enviado manda
                form["best_width"] = "best_width" in qp
            # las cajas de texto viajan siempre que el bloque se ve; si no hay ninguna (p. ej. se acaba de cambiar
            # de instrumento) los límites conservan sus valores marcados por defecto
            for shown_keys in (("rr", "mc", "ss", "cs", "lo", "ls", "mn"), ("ld", "cw", "wd")):
                if any(key in qp for key in shown_keys):
                    for key in shown_keys:
                        form[f"use_{key}"] = f"use_{key}" in qp
            for key in ("rr", "mc", "mn", "ss", "cs", "lo", "ls", "ld", "cw", "cwx", "wd"):
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
            legacy = {new: old for old, new in service.legacy_radar_source_names().items()}     # enlaces y perfiles con el nombre anterior de una fuente
            form["fsrc"] = [s.name for s in universe if f"fs_{s.name}" in qp or f"fs_{legacy.get(s.name)}" in qp]     # una fuente que ya no existe se ignora
            # con `src` (bloque enviado) manda la casilla «Filtrar por fuentes»: activa y sin ninguna marcada no sale nada;
            # una URL sin él (perfiles guardados antes) filtra solo si trae fuentes marcadas
            form["use_src"] = ("use_src" in qp) if "src" in qp else bool(form["fsrc"])
            if form["use_src"]:
                chosen = set(form["fsrc"])
                overrides["source_names"] = tuple(form["fsrc"])
                overrides["source_tickers"] = frozenset(clean_ticker(r.ticker) for s in universe if s.name in chosen for r in s.table.rows)
            if "fl" in qp:      # bloque enviado: lo marcado manda; una URL sin él (perfiles guardados antes) usa la calidad mínima inicial de la configuración
                form["fl_eps"], form["fl_fcf"] = "fl_eps" in qp, "fl_fcf" in qp
                form["fl_q"] = qp.get("fl_q", "0").strip()
                if form["fl_q"] not in ("0", "1", "2", "3", "4"):
                    raise ValueError("trimestres con beneficios: valor no permitido")
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
        overrides.update(floor_eps=bool(form["fl_eps"]), floor_quarters=int(form["fl_q"] or 0), floor_fcf=bool(form["fl_fcf"]))
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
            overrides["min_put_credit"], form["use_credit"] = None, False      # el crédito del spread lo fija «Crédito mín.» (use_mc)
            overrides["max_capital_risk_pct"], form["use_risk"] = None, False   # el capital en riesgo del spread es su «Pérdida máxima (% del NLV)» (use_mn)
            overrides.update(floor_eps=False, floor_quarters=0, floor_fcf=False)    # la calidad mínima es de la put desnuda; el spread tiene el riesgo acotado
            form.update(fl_eps=False, fl_q="0", fl_fcf=False)
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
            if form["use_ld"]:
                overrides["max_long_abs_delta"] = _required(form["ld"], float, "|Delta| máx. de la put comprada")
                if not (0 < overrides["max_long_abs_delta"] <= 1):
                    raise ValueError("|Delta| máx. de la put comprada debe estar entre 0 y 1")
            if form["use_cw"]:
                overrides["min_credit_width_pct"] = _required(form["cw"], float, "Crédito / ancho mín. (%)")
                if not (0 < overrides["min_credit_width_pct"] < 100):
                    raise ValueError("Crédito / ancho mín. (%) fuera de rango")
                if form["cwx"]:
                    overrides["max_credit_width_pct"] = _required(form["cwx"], float, "Crédito / ancho máx. (%)")
                    if not (overrides["min_credit_width_pct"] <= overrides["max_credit_width_pct"] < 100):
                        raise ValueError("Crédito / ancho máx. (%) tiene que estar entre el mínimo y 100")
            if form["use_wd"]:
                widths = tuple(sorted({_required(part, float, "Anchos fijos ($)") for part in form["wd"].replace(";", ",").split(",") if part.strip()}))
                if not widths or any(w <= 0 for w in widths):
                    raise ValueError("Anchos fijos ($): indica al menos un ancho positivo")
                overrides["spread_widths"] = widths
        criteria = base.with_filters(**overrides, exit_commission=service.profile_exits_early(qp.get("profile")))
        if criteria.strike_below_pct_min < cand.strike_below_pct_min:
            warnings.append(f"Descuento mín. por debajo del rango guardado ({cand.strike_below_pct_min:g}%): "
                            "no hay contratos con menos descuento.")
        if criteria.dte_max > cand.dte_max or criteria.dte_min < cand.dte_min:
            warnings.append(f"DTE fuera del rango guardado ({cand.dte_min}–{cand.dte_max} días): "
                            "no hay contratos fuera de él.")
    except ValueError as exc:
        error = f"Parámetro no válido: {exc}"
    return dict(form=form, criteria=criteria, error=error, warnings=warnings)
