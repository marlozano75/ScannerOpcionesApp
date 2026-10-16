"""Composición diaria del universo para bull put spreads. Funciones puras: la rutina trae las filas del Market Radar de Tortuga y aquí se
decide quién entra, con lo que el radar sabe y tastytrade no (VRP, rendimiento de la put de 30Δ, tendencia, movimiento esperado). Los
filtros finos del spread (crédito/ancho, bid/ask del combo, OI de las patas, delta, IV Rank, resultados…) los aplica luego el Scanner.

Un valor del radar es candidato si cumple TODO: liquidez de opciones del radar ≥ `radar_min_liquidity`, precio ≥ `price_min`, VRP > `min_vrp_pp`
(la IV sobreestima la volatilidad real: esa es la ventaja del vendedor), tendencia no bajista, movimiento esperado a 30 días ≤
`max_expected_move_pct` (descarta los que se mueven a saltos) y, solo en acciones, rendimiento anualizado de la put de 30Δ ≥ `min_put30_yield_pct`
(crédito potencial; los ETF de índice tienen IV baja por naturaleza y no lo cumplirían).
- Los ETF de la lista inicial que el radar no cubre (`radar=false`) son un núcleo fijo: siempre dentro.
- Entra quien es candidato `entry_days` días hábiles seguidos (por defecto 1) y sale quien deja de serlo `exit_days` días hábiles seguidos (por defecto 5),
  para que un ticker no entre y salga cada día por una métrica que oscila. Los fines de semana el radar no cambia: no cuentan.
- Nunca entran las exclusiones (ETF apalancadas o inversas, productos de VIX…).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Optional

from scanner_opciones.config.settings import SpreadUniverseSettings
from scanner_opciones.universe_bps.data import LEVELS, Member, UniverseBpsError, parse_member


@dataclass(frozen=True)
class RadarRow:
    ticker: str
    sector: str
    liquidity: Optional[int]
    price: Optional[float]
    kind: str = "Acción"
    vrp_pp: Optional[float] = None               # IV30 − RV20, en puntos
    put30_yield_pct: Optional[float] = None      # rendimiento anualizado de la put de 30Δ
    trend: Optional[str] = None                  # bullish | neutral | bearish
    expected_move_pct: Optional[float] = None    # movimiento esperado a 30 días
    iv30_pct: Optional[float] = None


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_radar(doc: Any) -> list[RadarRow]:
    """Salida de la rutina: {"version": 1, "rows": [{"ticker", "sector", "liquidity", "price", "is_etf", "vrp_pp", "put30_yield_pct",
    "trend", "expected_move_pct", "iv30_pct"}]}."""
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("rows"), list):
        raise UniverseBpsError('el radar debe ser {"version": 1, "rows": [...]}')
    rows = []
    for r in doc["rows"]:
        ticker = str(r.get("ticker", "")).strip().upper() if isinstance(r, dict) else ""
        if not ticker:
            continue
        liq = _num(r.get("liquidity"))
        trend = str(r["trend"]).lower() if isinstance(r.get("trend"), str) else None
        rows.append(RadarRow(
            ticker, str(r.get("sector") or ""), int(liq) if liq is not None else None, _num(r.get("price")),
            "ETF" if r.get("is_etf") or str(r.get("sector", "")).strip().upper() == "ETF" else "Acción",
            _num(r.get("vrp_pp")), _num(r.get("put30_yield_pct")), trend, _num(r.get("expected_move_pct")), _num(r.get("iv30_pct"))))
    if not rows:
        raise UniverseBpsError("el radar no trae ninguna fila")
    return rows


def parse_seed(doc: Any) -> tuple[list[Member], set[str]]:
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("members"), list):
        raise UniverseBpsError('la lista inicial debe ser {"version": 1, "members": [...]}')
    return [parse_member(m) for m in doc["members"]], {str(t).upper() for t in doc.get("excluded", [])}


def reject_reason(r: RadarRow, cfg: SpreadUniverseSettings) -> Optional[str]:
    """Por qué un valor del radar no es candidato, o None. Un dato que falta no descarta (lo decide luego el Scanner), salvo la liquidez."""
    if (r.liquidity or 0) < cfg.radar_min_liquidity:
        return f"liquidez del radar inferior a {cfg.radar_min_liquidity}"
    if r.price is not None and r.price < cfg.price_min:
        return f"precio inferior a {cfg.price_min:g}"
    if r.vrp_pp is not None and r.vrp_pp <= cfg.min_vrp_pp:
        return "VRP no positivo: la IV no supera a la volatilidad real"
    if cfg.exclude_bearish and r.trend == "bearish":
        return "tendencia bajista"
    if cfg.max_expected_move_pct is not None and r.expected_move_pct is not None and r.expected_move_pct > cfg.max_expected_move_pct:
        return f"movimiento esperado a 30 días superior a {cfg.max_expected_move_pct:g} %"
    if r.kind == "Acción" and r.put30_yield_pct is not None and r.put30_yield_pct < cfg.min_put30_yield_pct:
        return f"put de 30Δ por debajo del {cfg.min_put30_yield_pct:g} % anual"
    return None


def _note(r: RadarRow) -> str:
    parts = []
    if r.vrp_pp is not None:
        parts.append(f"VRP {r.vrp_pp:+.1f} pp")
    if r.put30_yield_pct is not None:
        parts.append(f"put 30Δ {r.put30_yield_pct:.0f} % anual")
    if r.trend:
        parts.append({"bullish": "alcista", "neutral": "neutral", "bearish": "bajista"}.get(r.trend, r.trend))
    return " · ".join(parts) or "Entra por el Market Radar"


def _member(r: RadarRow) -> Member:
    return Member(r.ticker, r.kind, r.sector, "A" if r.kind == "ETF" else "B", True, _note(r))


def update(
    history: dict, seed: list[Member], excluded: set[str], radar: list[RadarRow], day: date, cfg: SpreadUniverseSettings,
) -> tuple[dict, list[Member]]:
    """Devuelve (historial nuevo, miembros). Cada ticker cuenta como mucho una vez por día hábil, así que reconstruir el mismo día no duplica rachas
    (sí puede añadir un candidato nuevo si `entry_days` es 1). En sábado y domingo solo se refrescan los datos."""
    stamp, counts = day.isoformat(), day.weekday() < 5
    state = {k: dict(v) for k, v in history.get("tickers", {}).items()}
    fixed = {m.ticker: m for m in seed if not m.radar and m.ticker not in excluded}      # el radar no los cubre: núcleo fijo
    picks = {r.ticker: r for r in radar if r.ticker not in excluded and r.ticker not in fixed and reject_reason(r, cfg) is None}
    for t in set(state) | set(picks):
        s = state.setdefault(t, {"member": False, "days_in": 0, "days_out": 0})
        present = t in picks
        if present:
            s["data"] = asdict(_member(picks[t]))
        if s.get("day") == stamp or not counts:
            continue                                    # hoy ya contó, o es fin de semana
        s["day"] = stamp
        s["days_in"] = s["days_in"] + 1 if present else 0
        s["days_out"] = 0 if present else s["days_out"] + 1
        if s["member"] and s["days_out"] >= cfg.exit_days:
            s["member"] = False
        elif not s["member"] and s["days_in"] >= cfg.entry_days:
            s["member"] = True
    members = list(fixed.values())
    for t, s in state.items():
        if s["member"] and t not in excluded and t not in fixed and s.get("data"):
            members.append(parse_member(s["data"]))
    state = {t: s for t, s in state.items() if t not in fixed and (s["member"] or s["days_in"] > 0)}
    members.sort(key=lambda m: (LEVELS.index(m.level), m.ticker))
    return {"day": stamp, "tickers": state}, members


def warnings(members: list[Member], cfg: SpreadUniverseSettings) -> list[str]:
    out = []
    stocks = [m for m in members if m.kind == "Acción" and m.active]
    if stocks:
        sector, n = Counter(m.sector for m in stocks).most_common(1)[0]
        if n / len(stocks) * 100 > cfg.max_sector_pct:
            out.append(f"el sector «{sector}» tiene {n} de {len(stocks)} acciones (> {cfg.max_sector_pct:g} %)")
    return out
