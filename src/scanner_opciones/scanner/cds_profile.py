"""Parámetros por defecto del perfil «MTO CDS» (instrumento `cds` de los perfiles del Scanner). Funciones puras sobre `Settings`.

Las claves son las de los parámetros de la URL de `/cds` (`ui/cds_form.py`); los booleanos se guardan como «on» y los desmarcados no se guardan."""
from __future__ import annotations

from scanner_opciones.scanner.criteria import criteria_from_settings

MTO_CDS = "MTO CDS"
MTO_CDS_COLOR = "#b4532a"


def _fmt(v) -> str:
    return f"{v:g}"


def default_form(settings) -> dict:
    """Valores iniciales del formulario CDS: los de `scanner.cds` (config.yaml) y el precio de venta de referencia del Scanner."""
    cfg = settings.scanner.cds
    crit = criteria_from_settings(settings)
    return {
        "dte_min": str(cfg.dte_min), "dte_max": str(cfg.dte_max),
        "otm_min": _fmt(cfg.long_otm_pct_min), "otm_max": _fmt(cfg.long_otm_pct_max),
        "widths": ", ".join(_fmt(w) for w in cfg.width_options),
        "wpct_min": "" if cfg.width_pct_min is None else _fmt(cfg.width_pct_min),
        "wpct_max": "" if cfg.width_pct_max is None else _fmt(cfg.width_pct_max),
        "best": cfg.best_width,
        "dmin": _fmt(cfg.min_debit_usd), "dmax": _fmt(cfg.max_debit_usd),
        "oi": str(cfg.min_long_oi), "ls": _fmt(cfg.max_leg_spread_pct),
        "gain": _fmt(cfg.target_gain_pct), "days": str(cfg.move_days), "mult": _fmt(cfg.max_required_move_mult),
        "use_ret": cfg.min_max_return_pct is not None, "ret": _fmt(cfg.min_max_return_pct if cfg.min_max_return_pct is not None else 100),
        "use_sma": cfg.sma200_below_pct_min is not None, "sma": _fmt(cfg.sma200_below_pct_min if cfg.sma200_below_pct_min is not None else 10),
        "ref": crit.price_reference.value, "ref_x": _fmt(crit.price_spread_pct),
    }


def profile_params(settings) -> dict:
    """Parámetros de perfil (strings) con los valores de `scanner.cds`."""
    return {k: ("on" if v is True else str(v)) for k, v in default_form(settings).items() if v is not False}
