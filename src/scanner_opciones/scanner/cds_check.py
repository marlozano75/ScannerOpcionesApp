"""Consulta de un Call Debit Spread concreto (ticker + vencimiento + strike comprado y vendido): los parámetros que tendría en la salida del Scanner
CDS y, uno a uno, los filtros que cumple y los que no. Función pura sobre las calls guardadas.

El spread se construye con TODOS los filtros relajados (y una ventana de DTE/% OTM sin límites), de modo que existe aunque el Scanner lo descarte; cada
filtro activo del formulario se evalúa después como predicado (los mismos de `cds_impact.predicates`, así que consulta y panel de impacto no divergen).
Si el spread no puede construirse (falta una pata, delta, mercado válido…) se dice por qué en `problem`."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Optional, Sequence

from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.metrics.yields import reference_price
from scanner_opciones.scanner.cds import EPS, CdsResult, _spread_pct, build_call_debit_spreads, earnings_in_window, iv_at_horizon
from scanner_opciones.scanner.cds_impact import TickerFilter, predicates, relaxed


@dataclass(frozen=True)
class Check:
    key: str            # clave del filtro (las de `cds_impact.predicates` y `dte`/`otm`/`width`/`mode`/las de los filtros de ticker)
    label: str
    config: str         # lo que exige el formulario, en palabras
    value: str          # lo que tiene este spread
    ok: bool


@dataclass(frozen=True)
class CdsCheck:
    ticker: str
    expiry: Optional[date]
    long_strike: float
    short_strike: float
    result: Optional[CdsResult] = None
    checks: tuple[Check, ...] = ()
    problem: Optional[str] = None                 # por qué no se puede calcular el spread (sin `result`)
    in_scan: Optional[bool] = None                # aparece en el escaneo real (con «mejor ancho» y «máx. por ticker»)
    hints: tuple[str, ...] = ()                   # vencimientos / strikes guardados, cuando lo pedido no existe

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok)

    @property
    def failed_keys(self) -> frozenset[str]:
        return frozenset(c.key for c in self.failed)


def _fmt(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def _wide(cds: CdsSettings) -> CdsSettings:
    """Ajustes sin ningún filtro ni ventana: solo exigen que el spread se pueda calcular."""
    return relaxed(cds).model_copy(update=dict(dte_min=-10**6, dte_max=10**6, long_otm_pct_min=-1e6, long_otm_pct_max=1e6, all_widths=True))


def _problem(lg: Optional[ContractSnapshot], sh: Optional[ContractSnapshot], info: Optional[TickerInfo], cds: CdsSettings, today: date,
             price_reference: PriceReference, price_spread_pct: float, cost_ps: Callable[[float], float], iv_ok: bool) -> str:
    """La razón por la que `build_call_debit_spreads` no da este spread, en el orden en que lo descarta."""
    price = info.underlying_price if info else None
    if lg is None or sh is None:
        return "Falta la call " + ("comprada" if lg is None else "vendida") + " en la base: no está guardada (fuera de la ventana del catálogo) o aún no se ha cotizado."
    if sh.contract.strike <= lg.contract.strike:
        return "La call vendida debe tener un strike mayor que la comprada."
    if not price or price <= 0:
        return "El ticker no tiene precio del subyacente."
    if sh.contract.multiplier != lg.contract.multiplier:
        return "Las dos patas tienen multiplicador distinto."
    for name, s in (("comprada", lg), ("vendida", sh)):
        if _spread_pct(s) is None:
            return f"La call {name} no tiene mercado válido (bid y ask > 0)."
        if s.delta is None:
            return f"La call {name} no tiene delta."
    if not iv_ok:
        return "Sin IV (ni del ticker ni de la call comprada): no hay movimiento esperado."
    sell = reference_price(sh.bid, sh.ask, price_reference, price_spread_pct)
    if sell is None:
        return "No se puede calcular el precio de venta de la call vendida."
    width = sh.contract.strike - lg.contract.strike
    debit = lg.ask - sell + cost_ps(lg.contract.multiplier)
    if debit <= EPS:
        return f"El débito sería {_fmt(debit, 2)} (≤ 0): no es un spread de débito."
    if debit >= width:
        return f"El débito ({_fmt(debit, 2)}) no es menor que el ancho ({_fmt(width, 2)}): no puede ganar nada."
    if abs(lg.delta) - abs(sh.delta) <= EPS:
        return "La delta neta no es positiva: una subida del subyacente no da el objetivo."
    return f"El valor de salida (débito × {_fmt(1 + cds.target_gain_pct / 100, 2)}) supera el ancho: el objetivo no cabe en este spread."


def _values(r: CdsResult, info: Optional[TickerInfo], cds: CdsSettings, today: date) -> dict[str, str]:
    """Lo que tiene el spread para cada filtro de `cds_impact.predicates`."""
    mult = r.long.contract.multiplier
    sp_l, sp_s = _spread_pct(r.long), _spread_pct(r.short)
    earn = info.next_earnings if info else None
    sz = lambda a, b: f"comp. {a if a is not None else '—'} / vend. {b if b is not None else '—'}"
    return {
        "cds_debit": f"{_fmt(r.debit * mult, 0)} $",
        "cds_move": f"{_fmt(r.required_move_mult, 2)}× (necesario {_fmt(r.required_move_pct)} % · esperado {_fmt(r.expected_move_pct)} %)",
        "cds_prob": f"{_fmt(r.prob_pct, 0)} %",
        "cds_xprofit": f"{_fmt(r.exit_profit_usd, 0)} $",
        "cds_spread": f"{_fmt(sp_l)} %" if sp_l is not None else "—",
        "cds_spread_s": f"{_fmt(sp_s)} %" if sp_s is not None else "—",
        "cds_earn": (f"resultados el {earn:%d/%m/%Y}" if earn and earnings_in_window(info, cds.move_days, today) else "sin resultados en la ventana"),
        "cds_oi": str(r.long.open_interest if r.long.open_interest is not None else "—"),
        "cds_oi_s": str(r.short.open_interest if r.short.open_interest is not None else "—"),
        "cds_lvol": str(r.long.volume if r.long.volume is not None else "—"),
        "cds_svol": str(r.short.volume if r.short.volume is not None else "—"),
        "cds_bsz": sz(r.long.bid_size, r.short.bid_size),
        "cds_asz": sz(r.long.ask_size, r.short.ask_size),
        "cds_wpct": f"{_fmt(r.width / r.long.contract.strike * 100)} %",
        "cds_return": f"{_fmt(r.max_return_pct, 0)} %",
        "cds_sma": f"{_fmt(r.sma200_distance_pct)} %" if r.sma200_distance_pct is not None else "sin 200 sesiones",
    }


def check_cds(
    snapshots: Sequence[ContractSnapshot], infos: Mapping[str, TickerInfo], cds: CdsSettings, today: date,
    ticker: str, expiry: date, long_strike: float, short_strike: float,
    bars: Optional[Mapping[str, Sequence]] = None, price_reference: PriceReference = PriceReference.BID_PLUS_SPREAD, price_spread_pct: float = 25.0,
    cost_ps: Callable[[float], float] = lambda multiplier: 0.0, ticker_filters: Sequence[TickerFilter] = (),
    scan_rows: Optional[Sequence[CdsResult]] = None,
) -> CdsCheck:
    """`snapshots`: calls guardadas (las de otros tickers se ignoran). `ticker_filters`: filtros del ticker activos (calidad, técnicos…). `scan_rows`: las
    filas del escaneo real (con `best_width` y `top_per_ticker`) para decir si el spread aparecería; sin ellas `in_scan` queda en None."""
    mine = [s for s in snapshots if s.contract.ticker == ticker and s.contract.right is OptionRight.CALL]
    same = {round(s.contract.strike, 4): s for s in mine if s.contract.expiry == expiry}
    base = dict(ticker=ticker, expiry=expiry, long_strike=long_strike, short_strike=short_strike)
    lg, sh = same.get(round(long_strike, 4)), same.get(round(short_strike, 4))
    info = infos.get(ticker)

    hints: list[str] = []
    if lg is None or sh is None:
        expiries = sorted({s.contract.expiry for s in mine})
        hints.append("Vencimientos guardados: " + (", ".join(f"{e:%d/%m/%Y}" for e in expiries) if expiries else "ninguno (¿el ticker tiene calls guardadas?)"))
        if same:
            hints.append(f"Strikes guardados el {expiry:%d/%m/%Y}: " + ", ".join(f"{k:g}" for k in sorted(same)))

    wide = _wide(cds)
    found = next((r for r in build_call_debit_spreads(list(same.values()), infos, wide, today, bars, price_reference, price_spread_pct, cost_ps)
                  if abs(r.long.contract.strike - long_strike) < 1e-6 and abs(r.short.contract.strike - short_strike) < 1e-6), None)
    if found is None:
        iv_ok = bool((iv_at_horizon(info, cds.move_days, today) or (lg.iv if lg else None)))
        return CdsCheck(**base, problem=_problem(lg, sh, info, cds, today, price_reference, price_spread_pct, cost_ps, iv_ok), hints=tuple(hints))

    r = found
    dte = (expiry - today).days
    checks: list[Check] = [
        Check("dte", "DTE", f"{cds.dte_min}–{cds.dte_max} días", f"{dte} días", cds.dte_min <= dte <= cds.dte_max),
        Check("otm", "Call comprada OTM", f"{_fmt(cds.long_otm_pct_min, 0)}–{_fmt(cds.long_otm_pct_max, 0)} % sobre el precio",
              f"{_fmt(r.long_otm_pct)} %", cds.long_otm_pct_min - EPS <= r.long_otm_pct <= cds.long_otm_pct_max + EPS),
    ]
    if not cds.all_widths:
        allowed = any(abs(r.width - w) < 1e-6 for w in cds.width_options)
        checks.append(Check("width", "Ancho entre los elegidos", ", ".join(f"{w:g}" for w in cds.width_options), f"{r.width:g} $", allowed))
    values = _values(r, info, cds, today)
    for key, label, config, pred in predicates(cds, infos, today):
        checks.append(Check(key, label, config, values.get(key, ""), bool(pred(r))))
    for f in ticker_filters:
        checks.append(Check(f.key, f.label, f.config, "cumple" if f.passes(ticker) else "no cumple", bool(f.passes(ticker))))
    in_scan: Optional[bool] = None
    if scan_rows is not None:
        in_scan = any(x.long.contract.ticker == ticker and x.long.contract.expiry == expiry
                      and abs(x.long.contract.strike - long_strike) < 1e-6 and abs(x.short.contract.strike - short_strike) < 1e-6 for x in scan_rows)
    return CdsCheck(**base, result=r, checks=tuple(checks), in_scan=in_scan, hints=tuple(hints))
