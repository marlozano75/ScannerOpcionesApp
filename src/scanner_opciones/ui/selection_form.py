"""Formulario «Selección de tickers» de /scanner/config: lectura de lo escrito y valores planos para la plantilla (sin lógica de negocio)."""
from __future__ import annotations

from scanner_opciones.ui.scanner_forms import _read_num

# (clave de AppService.selection_config(), campo, etiqueta, tipo, escala del formulario): warm_ratio se escribe en % y se guarda en fracción
NUMBERS = (
    ("selection", "window_days", "Sesiones de la ventana", int, 1),
    ("selection", "min_days", "Sesiones mínimas para decidir", int, 1),
    ("selection", "min_hits", "Aciertos mínimos", int, 1),
    ("selection", "warm_ratio", "% de lo exigido para templado", float, 100),
    ("selection", "quarantine_days", "Días de cuarentena", int, 1),
    ("ticker_modes", "put_min_option_liquidity", "Liquidez mínima de las puts desnudas", int, 1),
    ("ticker_modes", "spread_min_option_liquidity", "Liquidez mínima de los spreads de puts", int, 1),
    ("ticker_modes", "cds_min_option_liquidity", "Liquidez mínima del CDS", int, 1),
    ("closing", "max_spread_pct", "Spread máximo para cerrar (%)", float, 1),
    ("closing", "min_oi", "OI mínimo para cerrar", int, 1),
    ("refresh", "margin_max_per_cycle", "Márgenes nuevos por ciclo (0 = sin tope)", int, 1),
)


def parse(form, current: dict, sources: list[str], profiles: list[str]) -> dict:
    """Valores del formulario sobre la configuración actual (`current`, de `AppService.selection_config`). Lanza ValueError si un número no lo es.
    `source_profiles`: las fuentes que están en la tabla se sustituyen por lo marcado; las demás se conservan."""
    out = {section: dict(values) for section, values in current.items()}
    out["selection"]["enabled"] = form.get("enabled") is not None
    out["selection"]["warm_quote_once_a_day"] = form.get("warm_once") is not None
    for section, key, label, cast, scale in NUMBERS:
        raw = form.get(f"f_{key}")
        if raw is None:
            continue
        value = _read_num(raw, cast, label)
        out[section][key] = value / scale if scale != 1 else value
    mapping = {s: list(p) for s, p in current["selection"].get("source_profiles", {}).items() if s not in sources}
    for i, source in enumerate(sources):
        picked = [p for j, p in enumerate(profiles) if form.get(f"sp_{i}_{j}") is not None]
        if picked:
            mapping[source] = picked
    out["selection"]["source_profiles"] = mapping
    return out


def flat(cfg: dict) -> dict:
    """Valores planos para rellenar el formulario."""
    flat_values = {"enabled": cfg["selection"]["enabled"], "warm_once": cfg["selection"]["warm_quote_once_a_day"]}
    for section, key, _label, _cast, scale in NUMBERS:
        value = cfg[section][key]
        flat_values[key] = round(value * scale, 4) if scale != 1 else value
    return flat_values
