"""Radar de Bull Put Spreads de Tortuga: los valores del Market Radar cuya estrategia propuesta es «Bull Put Spread».

Una rutina (ver `routines.py`) pide al conector la lista (`get_market_radar` con `strategy=bull_put_spread`) y deja un JSON con las filas
tal como las devuelve la herramienta. Formato (versión 1):

    {"version": 1, "generated_at": "2026-10-17T11:45:00+02:00", "as_of": "2026-10-16", "matched": 59, "returned": 59,   # la app lo arma con las 4 llamadas por cuadrante
     "rows": [ {"symbol": "MPC", "sector": "Energy", "price": 476.65, "iv_rank": 97.3, ..., "put30": {"strike": 440, ...}}, ... ]}

La lista del radar NO trae los strikes del spread que propone (solo su ficha, una por valor) sino la put de 30Δ de referencia: aquí se
usa esa. Un dato que falta queda en None. Comprobación: `python -m scanner_opciones.tortuga.radar fichero.json`."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

VERSION = 1


class RadarError(ValueError):
    """El JSON del radar no existe o no tiene el formato esperado."""


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class RadarRow:
    ticker: str
    sector: Optional[str] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    iv30_pct: Optional[float] = None
    iv_change_pp: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    rv20_pct: Optional[float] = None
    vrp_pp: Optional[float] = None
    iv_hv: Optional[float] = None
    term_structure: Optional[str] = None
    skew25d_pp: Optional[float] = None
    expected_move_pct: Optional[float] = None
    rsi14: Optional[float] = None
    trend: Optional[str] = None
    earnings_date: Optional[date] = None
    earnings_days: Optional[int] = None
    earnings_before_expiry: Optional[bool] = None
    zone: Optional[str] = None
    strategy: Optional[str] = None
    score: Optional[float] = None
    signal: Optional[str] = None
    liquidity_stars: Optional[float] = None
    liquidity_spread_pct: Optional[float] = None
    option_volume: Optional[float] = None
    volume_ratio: Optional[float] = None
    total_oi: Optional[float] = None
    put_call_oi: Optional[float] = None
    put_call_volume: Optional[float] = None
    put_wall: Optional[float] = None
    call_wall: Optional[float] = None
    max_pain: Optional[float] = None
    put_strike: Optional[float] = None      # put de 30Δ de referencia
    put_mid: Optional[float] = None
    put_delta: Optional[float] = None
    put_yield_annual_pct: Optional[float] = None
    put_expiry: Optional[date] = None
    put_dte: Optional[int] = None

    @property
    def put_yield_period_pct(self) -> Optional[float]:
        """Prima / strike de la put de 30Δ hasta su vencimiento, sin anualizar (el yield del periodo de los perfiles)."""
        if self.put_mid is None or not self.put_strike or self.put_strike <= 0:
            return None
        return self.put_mid / self.put_strike * 100

    @property
    def distance_pct(self) -> Optional[float]:
        """% del strike de 30Δ por debajo del precio."""
        if self.put_strike is None or not self.price or self.price <= 0:
            return None
        return (self.price - self.put_strike) / self.price * 100


def _opt_int(value: Any) -> Optional[int]:
    n = _num(value)
    return None if n is None else int(n)


def _parse_row(item: Mapping) -> RadarRow:
    ticker = str(item["symbol"]).strip().upper()
    if not ticker:
        raise ValueError("symbol vacío")
    top = item.get("top_strategy") or {}
    liq = item.get("liquidity") or {}
    levels = item.get("levels") or {}
    p30 = item.get("put30") or {}
    earn = item.get("earnings") or {}
    return RadarRow(
        ticker=ticker, sector=item.get("sector"), price=_num(item.get("price")), change_pct=_num(item.get("change_pct")),
        iv30_pct=_num(item.get("iv30_pct")), iv_change_pp=_num(item.get("iv_change_pp")), iv_rank=_num(item.get("iv_rank")),
        iv_percentile=_num(item.get("iv_percentile")), rv20_pct=_num(item.get("rv20_pct")), vrp_pp=_num(item.get("vrp_pp")),
        iv_hv=_num(item.get("iv_hv")), term_structure=item.get("term_structure"), skew25d_pp=_num(item.get("skew25d_pp")),
        expected_move_pct=_num(item.get("expected_move_30d_pct")), rsi14=_num(item.get("rsi14")), trend=item.get("trend"),
        earnings_date=_day(earn.get("date")), earnings_days=_opt_int(earn.get("days_to")),
        earnings_before_expiry=earn.get("before_target_expiry") if isinstance(earn.get("before_target_expiry"), bool) else None,
        zone=item.get("zone"), strategy=top.get("name"), score=_num(top.get("score")), signal=item.get("signal") or top.get("signal"),
        liquidity_stars=_num(liq.get("stars")), liquidity_spread_pct=_num(liq.get("spread_pct")), option_volume=_num(liq.get("option_volume")),
        volume_ratio=_num(liq.get("volume_ratio_20")), total_oi=_num(liq.get("total_oi")), put_call_oi=_num(liq.get("put_call_oi")),
        put_call_volume=_num(liq.get("put_call_volume")), put_wall=_num(levels.get("put_wall")), call_wall=_num(levels.get("call_wall")),
        max_pain=_num(levels.get("max_pain")), put_strike=_num(p30.get("strike")), put_mid=_num(p30.get("mid")), put_delta=_num(p30.get("delta")),
        put_yield_annual_pct=_num(p30.get("annualized_yield_pct")), put_expiry=_day(p30.get("expiry")), put_dte=_opt_int(p30.get("dte")),
    )


@dataclass(frozen=True)
class RadarData:
    generated_at: datetime
    as_of: Optional[date]
    rows: tuple[RadarRow, ...]
    warnings: tuple[str, ...] = field(default=())


def parse_radar(raw: object) -> RadarData:
    if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("rows"), list):
        raise RadarError(f"no es un JSON del radar de la versión {VERSION}")
    try:
        generated = datetime.fromisoformat(str(raw["generated_at"]))
    except (KeyError, ValueError):
        raise RadarError("falta generated_at (fecha ISO)") from None
    matched, returned = _opt_int(raw.get("matched")), _opt_int(raw.get("returned"))
    rows_raw = raw["rows"]
    # una respuesta recortada (el modelo se saltó filas) cambiaría la fuente y la watchlist: se rechaza entera
    if matched is None or returned is None or returned != len(rows_raw) or returned != matched:
        raise RadarError(f"respuesta incompleta: matched={matched}, returned={returned}, filas={len(rows_raw)}")
    rows: list[RadarRow] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(rows_raw):
        try:
            row = _parse_row(item)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            warnings.append(f"rows[{i}] descartada: {exc}")
            continue
        if row.ticker in seen:
            continue
        seen.add(row.ticker)
        rows.append(row)
    return RadarData(generated, _day(raw.get("as_of")), tuple(rows), tuple(warnings))


def load_radar(path: "str | Path") -> RadarData:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RadarError(f"no se pudo leer {path}: {exc}") from exc
    return parse_radar(raw)


# La herramienta corta cada respuesta en ~41 filas aunque se pida limit=100 (medido el 2026-10-17: matched=45, filas=41), y no tiene paginación.
# Por eso, si la lista entera no cabe, se pide por tramos de precio (todo valor tiene precio, así que los tramos lo cubren entero), partiendo
# solo los tramos que sigan recortados (lo decide el prompt, `scripts/radar_spreads_prompt.md`), y se comprueba contra el total.
COUNT_ARGS = {"strategy", "limit", "sort"}      # una llamada con solo estos argumentos no lleva ningún filtro: su `matched` es el total


def build_radar_document(calls: list, now: datetime) -> dict:
    """Documento de la versión 1 a partir de las llamadas a `get_market_radar` con `strategy=bull_put_spread`, con lo que la herramienta
    devolvió: una llamada SIN filtros (de ahí sale el total `matched`; si no viene recortada ya es la lista entera) y, si hace falta, las de
    los tramos de precio. Las respuestas recortadas aportan sus filas (son reales), pero se rechaza todo si, juntas y sin duplicados, no suman
    exactamente el total."""
    rows: list[dict] = []
    seen: set[str] = set()
    total: Optional[int] = None
    as_of = None
    for call in calls:
        args = getattr(call, "input", None) or {}
        if getattr(call, "name", "") != "get_market_radar" or args.get("symbol"):
            continue
        try:
            result = json.loads(call.output)
        except ValueError:
            continue
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            continue
        as_of = as_of or result.get("as_of")
        if not set(args) - COUNT_ARGS:
            total = _opt_int(result.get("matched"))          # la llamada sin filtros: da el total
        for item in result["rows"]:
            sym = str(item.get("symbol", "")).upper() if isinstance(item, dict) else ""
            if sym and sym not in seen:
                seen.add(sym)
                rows.append(item)
    if total is None:
        raise RadarError("falta la llamada de recuento del radar")
    if len(rows) != total:
        raise RadarError(f"el radar suma {len(rows)} valores y debería haber {total}: respuesta recortada o faltan tramos de precio")
    rows.sort(key=lambda r: -(_num(r.get("iv_rank")) or -1))
    return {"version": VERSION, "generated_at": now.astimezone().isoformat(timespec="seconds"), "source": "tortuga-market-radar",
            "as_of": as_of, "matched": len(rows), "returned": len(rows), "rows": rows}


def passes(row: RadarRow, min_yield_pct: float) -> bool:
    """¿La put de 30Δ rinde en el periodo al menos el yield que exige el perfil? Sin dato no cumple."""
    y = row.put_yield_period_pct
    return y is not None and y >= min_yield_pct - 1e-9


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        print("uso: python -m scanner_opciones.tortuga.radar fichero.json", file=sys.stderr)
        return 2
    try:
        data = load_radar(argv[0])
    except RadarError as exc:
        print(f"NO VÁLIDO: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(data.rows)} valores, datos del {data.as_of}, generado {data.generated_at.isoformat()}")
    for w in data.warnings:
        print(f"aviso: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
