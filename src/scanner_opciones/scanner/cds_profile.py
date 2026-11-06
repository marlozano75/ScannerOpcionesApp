"""Parámetros por defecto del perfil «MTO CDS» (instrumento `cds` de los perfiles del Scanner). Funciones puras sobre `Settings`.

Las claves son las de los parámetros de la URL del Scanner con `mode=cds` (`ui/cds_form.py`); los booleanos se guardan como «on» y los desmarcados no se guardan."""
from __future__ import annotations

from scanner_opciones.scanner.criteria import MA_CROSSES, MA_LINES, MA_SLOPES, criteria_from_settings

MTO_CDS = "MTO CDS"
MTO_CDS_COLOR = "#b4532a"


def _fmt(v) -> str:
    return f"{v:g}"


# filtros técnicos del CDS (mismos nombres que en el Scanner de puts): señales de entrada y medias móviles / EMA. Empiezan sin filtrar nada.
ENTRY_KEYS = ("signals", "use_near", "near_pct", "use_rsi", "rsi_max", "use_boll", "boll_pctb", "use_bw", "boll_width")
MA_KEYS = ("ma_frame", *MA_LINES, *MA_CROSSES, *MA_SLOPES)


def default_form(settings) -> dict:
    """Valores iniciales del formulario CDS: los de `scanner.cds` (config.yaml) y el precio de venta de referencia del Scanner."""
    cfg = settings.scanner.cds
    st = settings.scanner.strategy
    crit = criteria_from_settings(settings)
    return {
        "dte_min": str(cfg.dte_min), "dte_max": str(cfg.dte_max),
        "otm_min": _fmt(cfg.long_otm_pct_min), "otm_max": _fmt(cfg.long_otm_pct_max),
        "widths": ", ".join(_fmt(w) for w in cfg.width_options),
        "wpct_min": "" if cfg.width_pct_min is None else _fmt(cfg.width_pct_min),
        "wpct_max": "" if cfg.width_pct_max is None else _fmt(cfg.width_pct_max),
        "best": cfg.best_width,
        "dmin": _fmt(cfg.min_debit_usd), "dmax": _fmt(cfg.max_debit_usd),
        "oi": str(cfg.min_long_oi), "oi_s": str(cfg.min_short_oi), "ls": _fmt(cfg.max_long_spread_pct), "ls_s": _fmt(cfg.max_short_spread_pct),
        "use_bsz": cfg.min_bid_size is not None, "bsz": str(cfg.min_bid_size if cfg.min_bid_size is not None else 10),
        "use_asz": cfg.min_ask_size is not None, "asz": str(cfg.min_ask_size if cfg.min_ask_size is not None else 10),
        "gain": _fmt(cfg.target_gain_pct), "days": str(cfg.move_days), "mult": _fmt(cfg.max_required_move_mult),
        "use_prob": cfg.min_prob_pct is not None, "prob": _fmt(cfg.min_prob_pct if cfg.min_prob_pct is not None else 40), "top": str(cfg.top_per_ticker),
        "use_earn": cfg.exclude_earnings,
        "use_xp": cfg.min_exit_profit_usd is not None, "xp": _fmt(cfg.min_exit_profit_usd if cfg.min_exit_profit_usd is not None else 50),
        "use_ret": cfg.min_max_return_pct is not None, "ret": _fmt(cfg.min_max_return_pct if cfg.min_max_return_pct is not None else 100),
        "use_sma": cfg.sma200_below_pct_min is not None, "sma": _fmt(cfg.sma200_below_pct_min if cfg.sma200_below_pct_min is not None else 10),
        "ref": crit.price_reference.value, "ref_x": _fmt(crit.price_spread_pct),
        # técnicos de entrada y medias: desmarcados, con los valores de `scanner.strategy` en las cajas
        "signals": "0", "use_near": False, "near_pct": _fmt(st.near_level_pct), "use_rsi": False, "rsi_max": _fmt(st.max_rsi_level),
        "use_boll": False, "boll_pctb": _fmt(st.max_bollinger_pctb), "use_bw": False, "boll_width": _fmt(st.min_bollinger_width_pct),
        "ma_frame": "daily", **{k: "any" for k in (*MA_LINES, *MA_CROSSES, *MA_SLOPES)},
        # empresa y ticker: sin filtros por defecto (el perfil guarda los que elijas)
        "q_profit": False, "q_fcf": False, "q_quarters": "", "q_liq": "", "q_lev": "", "price_min": "", "price_max": "", "fsrc": [], "use_src": False,
    }


def profile_params(settings) -> dict:
    """Parámetros de perfil (strings) con los valores de `scanner.cds`."""
    return {k: ("on" if v is True else str(v)) for k, v in default_form(settings).items() if v is not False and k not in ("fsrc", "use_src", "q_quarters", "q_liq", "q_lev", "price_min", "price_max")}
