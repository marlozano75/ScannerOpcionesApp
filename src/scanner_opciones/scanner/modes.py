"""Modos por ticker: qué motores pueden dar resultados con un ticker según su precio, su IV y su liquidez (funciones puras).

`put` (puts desnudas), `put_spread` (spreads de puts) y `cds` (Call Debit Spread). Se evalúan con datos que ya están guardados y deciden
qué se cotiza y qué se escanea (`jobs/refresh.py`, `app/service.py`). Ver `TickerModesSettings`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Optional

from scanner_opciones.config.settings import TickerModesSettings
from scanner_opciones.domain.models import TickerInfo

MODE_NAMES = ("put", "put_spread", "cds")


@dataclass(frozen=True)
class TickerModes:
    put: bool = True
    put_spread: bool = True
    cds: bool = True
    reasons: dict = field(default_factory=dict)    # modo apagado -> motivo

    @property
    def any_puts(self) -> bool:
        """¿Hay que cotizar sus puts? (las necesitan las puts desnudas y los spreads de puts)"""
        return self.put or self.put_spread

    @property
    def all_on(self) -> bool:
        return self.put and self.put_spread and self.cds

    @property
    def any(self) -> bool:
        return self.put or self.put_spread or self.cds

    def reasons_json(self) -> str:
        return json.dumps(self.reasons, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def load_reasons(text: Optional[str]) -> dict:
        try:
            value = json.loads(text) if text else {}
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}


ALL_ON = TickerModes()


def derive_put_price_cap(criteria: Iterable, nlv_usd: Optional[float], rules: TickerModesSettings, multiplier: float = 100.0) -> Optional[float]:
    """Precio máximo del subyacente para vender una put desnuda con el NLV: el MÁS ALTO (perfil menos restrictivo) entre los perfiles de put que
    tienen activo el límite de capital en riesgo. Capital en riesgo ≈ strike × multiplicador, y el strike mínimo es precio × (1 − descuento mín.).
    `rules.put_max_price` manda si está fijado. None = no se puede derivar (sin NLV o ningún perfil limita el capital)."""
    if rules.put_max_price is not None:
        return rules.put_max_price
    if not nlv_usd or nlv_usd <= 0:
        return None
    caps = []
    for c in criteria:
        risk = getattr(c, "max_capital_risk_pct", None)
        if risk is None:
            continue
        discount = min(max(float(getattr(c, "strike_below_pct_min", 0) or 0), 0.0), 99.0)
        caps.append(risk / 100.0 * nlv_usd / (multiplier * (1 - discount / 100.0)))
    if not caps:
        return None
    return max(caps) * (1 + rules.put_price_margin_pct / 100.0)


def _iv_ok(info: TickerInfo, min_rank: Optional[float], min_pct: Optional[float], slack: float) -> tuple[bool, str]:
    """IV Rank o IV Percentile (basta uno); sin ningún mínimo no se exige nada. `slack` rebaja los mínimos (histéresis)."""
    if min_rank is None and min_pct is None:
        return True, ""
    ok = False
    if min_rank is not None and info.iv_rank is not None and info.iv_rank >= min_rank - slack:
        ok = True
    if min_pct is not None and info.iv_percentile is not None and info.iv_percentile >= min_pct - slack:
        ok = True
    if ok:
        return True, ""
    if info.iv_rank is None and info.iv_percentile is None:
        return False, "sin IV Rank ni IV Percentile"
    wanted = " o ".join(x for x in (f"IV Rank ≥ {min_rank:g}" if min_rank is not None else "", f"IV Percentile ≥ {min_pct:g}" if min_pct is not None else "") if x)
    return False, f"IV baja ({wanted})"


def evaluate(info: Optional[TickerInfo], rules: TickerModesSettings, put_price_cap: Optional[float], previous: Optional[TickerModes] = None) -> TickerModes:
    """Modos de un ticker. Un dato que falta apaga el modo que lo necesita. La IV de un modo que ya estaba encendido se evalúa con la
    holgura `iv_hysteresis_pts` (no se apaga y se enciende por pequeñas oscilaciones)."""
    if info is None or not info.underlying_price:
        why = "sin precio"
        return TickerModes(False, False, False, {m: why for m in MODE_NAMES})
    reasons: dict = {}
    cap = put_price_cap if put_price_cap is not None else rules.put_max_price_fallback
    put = info.underlying_price <= cap
    if not put:
        reasons["put"] = f"precio {info.underlying_price:g} $ por encima del máximo para puts desnudas ({cap:.0f} $, por el NLV)"
    liq = info.option_liquidity
    # puts desnudas: sin dato de liquidez no se bloquea (solo un nivel conocido y bajo apaga el modo)
    if put and liq is not None and liq < rules.put_min_option_liquidity:
        put = False
        reasons["put"] = f"liquidez de opciones {liq} (mín. {rules.put_min_option_liquidity}): difícil de recomprar"

    def liquid_for(minimum: int) -> tuple[bool, str]:
        if liq is None:
            return False, "sin dato de liquidez de opciones"
        return liq >= minimum, f"liquidez de opciones {liq} (mín. {minimum}): difícil de recomprar"

    ps_liquid, ps_liq_why = liquid_for(rules.spread_min_option_liquidity)
    cds_liquid, cds_liq_why = liquid_for(rules.cds_min_option_liquidity)
    slack_ps = rules.iv_hysteresis_pts if previous is not None and previous.put_spread else 0.0
    slack_cds = rules.iv_hysteresis_pts if previous is not None and previous.cds else 0.0
    iv_ps, iv_ps_why = _iv_ok(info, rules.put_spread_min_iv_rank, rules.put_spread_min_iv_percentile, slack_ps)
    iv_cds, iv_cds_why = _iv_ok(info, rules.cds_min_iv_rank, rules.cds_min_iv_percentile, slack_cds)
    put_spread = ps_liquid and iv_ps
    cds = cds_liquid and iv_cds
    if not put_spread:
        reasons["put_spread"] = ps_liq_why if not ps_liquid else iv_ps_why
    if not cds:
        reasons["cds"] = cds_liq_why if not cds_liquid else iv_cds_why
    return TickerModes(put, put_spread, cds, reasons)
