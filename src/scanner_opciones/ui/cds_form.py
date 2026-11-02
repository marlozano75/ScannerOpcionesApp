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
from scanner_opciones.ui.scanner_forms import _fmt

# claves de la URL que identifican la página y no son filtros del perfil
STRUCT = frozenset(("submitted", "mode", "profile", "reset", "message"))


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


def parse_cds(service, qp) -> dict:
    """Lee el formulario CDS. Devuelve `form` (valores para pintar), `cds` (`CdsSettings` o None si hay error), `ref` (precio de venta de referencia:
    `price_reference`, `price_spread_pct`), `notices` (valores recortados a la ventana guardada) y `error`."""
    catalog = service.settings.scanner.cds
    form = default_form(service)
    submitted = "submitted" in qp
    notices: list[str] = []
    cds: Optional[CdsSettings] = None
    ref: dict = {}
    error: Optional[str] = None
    if submitted:
        for key in ("dte_min", "dte_max", "otm_min", "otm_max", "widths", "wpct_min", "wpct_max", "dmin", "dmax", "oi", "ls", "gain", "days", "mult",
                    "ret", "sma", "ref", "ref_x"):
            if key in qp:
                form[key] = str(qp.get(key, "")).strip()
        for key in ("best", "use_ret", "use_sma"):          # casillas: ausentes = desmarcadas (solo si el formulario se envió entero)
            form[key] = key in qp
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
        if not widths:
            raise ValueError("Elige al menos un ancho de los que guarda el catálogo: " + ", ".join(_fmt(w) for w in catalog.width_options))
        cds = catalog.model_copy(update=dict(
            dte_min=dte_min, dte_max=dte_max, long_otm_pct_min=otm_min, long_otm_pct_max=otm_max, width_options=widths,
            width_pct_min=_num(form["wpct_min"], "Ancho mínimo (%)", allow_empty=True), width_pct_max=_num(form["wpct_max"], "Ancho máximo (%)", allow_empty=True),
            best_width=bool(form["best"]),
            min_debit_usd=_num(form["dmin"], "Débito mínimo"), max_debit_usd=_num(form["dmax"], "Débito máximo"),
            min_long_oi=_num(form["oi"], "OI mínimo", int), max_leg_spread_pct=_num(form["ls"], "Spread máximo de las patas"),
            target_gain_pct=_num(form["gain"], "Objetivo de ganancia"), move_days=_num(form["days"], "Días del movimiento", int),
            max_required_move_mult=_num(form["mult"], "Múltiplo del movimiento esperado"),
            min_max_return_pct=_num(form["ret"], "Retorno máximo mínimo") if form["use_ret"] else None,
            sma200_below_pct_min=_num(form["sma"], "SMA200") if form["use_sma"] else None))
        CdsSettings.model_validate(cds.model_dump())           # los mismos controles que la configuración
        if form["ref"] not in {r.value for r in PriceReference}:
            raise ValueError("precio de venta de referencia desconocido")
        ref = dict(price_reference=PriceReference(form["ref"]), price_spread_pct=_num(form["ref_x"], "X del precio de referencia"))
    except (ValueError, ValidationError) as exc:
        error = str(exc).replace("\n", " ")
        cds = None
    return dict(form=form, cds=cds, ref=ref, notices=notices, error=error, submitted=submitted)


def form_query(form: dict) -> dict:
    """Parámetros de la URL que reproducen `form` (para guardar en un perfil o recargar)."""
    return {k: ("on" if v is True else str(v)) for k, v in form.items() if v is not False}
