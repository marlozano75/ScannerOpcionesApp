"""Indicador informativo de «capitulación de mercado» (2026-11-08). NO es un filtro del scanner ni una señal de compra.

De la investigación de suelos (`docs/deteccion-suelos.md`, pestaña Backtest): las señales por ticker no dan ventaja, pero el estrés del mercado
(VIX en backwardation, mucha parte de la cesta líquida bajo su banda inferior de Keltner) mejora la probabilidad de que un CDS comprado
ese día acabe por encima a 10 sesiones. Medido con las dos condiciones (VIX>VIX3M y ≥ 20 % de la cesta bajo Keltner; 174 días, 2004-2026): cierra
por encima a 10 d el 61,3 % (base 55,9 %), toca +1,5 ATR el 64,7 % (base 51,9 %), gana la carrera el 56,0 % (base 47,2 %). Función pura sobre las velas y el VIX.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.config.settings import CapitulationSettings
from scanner_opciones.domain.models import VixData

KELTNER_EMA, KELTNER_ATR, KELTNER_MULT = 20, 10, 2.0
MAX_STALE_DAYS = 5                      # la última vela no puede ser más antigua (días naturales)

Candle = tuple  # (día, apertura, máximo, mínimo, cierre)


@dataclass(frozen=True)
class Capitulation:
    level: str                           # ok (las dos condiciones) | info (una) | none (ninguna) | na (sin datos)
    breadth_pct: Optional[float]         # % de la cesta bajo la banda inferior de Keltner
    breadth_n: int                       # tickers con datos suficientes
    backwardation: Optional[bool]        # VIX por encima del primer futuro
    text: str

    @property
    def title(self) -> str:
        return {"ok": "Capitulación de mercado", "info": "Capitulación parcial", "none": "Sin capitulación"}.get(self.level, "Capitulación")


def below_keltner(candles: Sequence[Candle], today: date) -> Optional[bool]:
    """¿El último cierre está bajo EMA20 − 2·ATR10 (media simple del rango verdadero)? None sin velas completas o desactualizadas."""
    bars = [c for c in candles if c[2] is not None and c[3] is not None]
    if len(bars) < KELTNER_EMA + KELTNER_ATR or (today - bars[-1][0]).days > MAX_STALE_DAYS:
        return None
    bars = bars[-250:]
    closes = [c[4] for c in bars]
    k = 2 / (KELTNER_EMA + 1)
    ema = closes[0]
    for px in closes[1:]:
        ema += k * (px - ema)
    tr = [max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])) for p, b in zip(bars, bars[1:])]
    atr = sum(tr[-KELTNER_ATR:]) / KELTNER_ATR
    return closes[-1] < ema - KELTNER_MULT * atr


def assess(vix: Optional[VixData], flags: Sequence[Optional[bool]], cfg: CapitulationSettings) -> Capitulation:
    known = [f for f in flags if f is not None]
    breadth = 100 * sum(known) / len(known) if len(known) >= cfg.min_tickers else None
    ref, ref_name = None, ""
    if vix is not None and vix.current is not None:
        if vix.vix3m:
            ref, ref_name = vix.vix3m, "VIX3M"
        elif vix.futures:
            ref, ref_name = vix.futures[0][1], "futuro a un mes"       # sin VIX3M (Yahoo caído): sustituto parecido pero no medido
    back = vix.current > ref if ref is not None else None
    if breadth is None and back is None:
        return Capitulation("na", None, len(known), None, "Sin datos suficientes para medir la capitulación del mercado.")
    wide = breadth is not None and breadth >= cfg.breadth_min_pct
    n_ok = int(wide) + int(bool(back))
    parts = []
    if breadth is not None:
        parts.append(f"{breadth:.0f} % de la cesta ({len(known)} valores líquidos) bajo Keltner" + (f", ≥ {cfg.breadth_min_pct:g} %" if wide else f", umbral {cfg.breadth_min_pct:g} %"))
    if back is not None:
        parts.append(f"VIX por encima del {ref_name} (backwardation)" if back else f"VIX por debajo del {ref_name} (contango)")
    detail = "; ".join(parts) + "."
    if n_ok == 2:
        return Capitulation("ok", breadth, len(known), back, f"{detail} En el histórico (2004-2026), con estas dos condiciones un CDS comprado ese día cerró por encima a 10 sesiones "
                            "el 61 % de las veces (56 % un día normal) y tocó +1,5 ATR el 65 % (52 %). Solo informa: es efecto del régimen de mercado y no hay ventaja por ticker.")
    if n_ok == 1:
        return Capitulation("info", breadth, len(known), back, f"{detail} Con una sola condición no hay ventaja medible.")
    return Capitulation("none", breadth, len(known), back, detail)
