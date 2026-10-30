"""Ideas de la comunidad re-valoradas con los datos de hoy.

Los trades de Tortuga llegan un día tarde (IBKR Flex): cuando se ven, el precio y las primas ya son otros. Esta función compara
cada idea con lo que hay guardado hoy (precio del subyacente, cotización de las patas, cierre del día en que se abrió) y dice si
sigue siendo la misma operación. No inventa nada: sin la cotización guardada (ticker fuera de la watchlist) no valora la prima.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Sequence

from scanner_opciones.community.data import CommunityIdea
from scanner_opciones.domain.enums import PriceReference
from scanner_opciones.domain.models import ContractSnapshot, TickerInfo
from scanner_opciones.metrics.commissions import CommissionModel
from scanner_opciones.metrics.yields import reference_price
from scanner_opciones.portfolio.openings import Close, norm_ticker

EXPIRED, NO_DATA, ITM, DROPPED, LOWER, VALID = "expired", "no_data", "itm", "dropped", "lower", "valid"
SNAPSHOT_KEY = tuple[str, date, float]


def idea_key(idea: CommunityIdea) -> str:
    """Identificador estable de una idea (para marcarla en la tabla y volver a encontrarla al enviar el formulario)."""
    long = "" if idea.long_strike is None else f"{idea.long_strike:g}"
    return f"{idea.ticker}|{idea.trader}|{idea.day.isoformat()}|{idea.expiry.isoformat()}|{idea.short_strike:g}|{long}"


def snapshot_key(ticker: str, expiry: date, strike: float) -> SNAPSHOT_KEY:
    return ticker, expiry, round(float(strike), 4)


@dataclass(frozen=True)
class PastTrade:
    """Una put vendida tuya en ese valor que ya cerraste (por ejemplo, recogiendo beneficios pronto)."""
    day: date
    days_ago: int
    pct_captured: Optional[float]
    pnl: Optional[float]
    same_contract: bool         # mismo vencimiento y strike que la idea (si no, solo el mismo ticker)


@dataclass(frozen=True)
class IdeaView:
    idea: CommunityIdea
    dte: int
    status: str
    text: str
    in_watchlist: bool
    price_open: Optional[float] = None      # cierre del día en que se abrió (aproxima el precio de ejecución)
    price_now: Optional[float] = None
    move_pct: Optional[float] = None        # variación del subyacente desde entonces
    otm_open_pct: Optional[float] = None    # % del strike vendido por debajo del precio, al abrir y hoy
    otm_now_pct: Optional[float] = None
    yield_open_pct: Optional[float] = None  # prima cobrada / strike (put) o / ancho (spread)
    premium_now: Optional[float] = None     # prima neta por acción que se cobraría hoy (en un spread, vendida − ask de la comprada)
    yield_now_pct: Optional[float] = None
    delta_now: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    fits: tuple = ()                        # `fit.Fit` de cada perfil del Scanner (los pone el servicio)
    traded: Optional[PastTrade] = None      # tu cierre más reciente de una put vendida en este ticker (lo pone el servicio)

    @property
    def risk_base(self) -> float:
        i = self.idea
        return i.short_strike - i.long_strike if i.long_strike is not None else i.short_strike


def _close_on_or_before(closes: Sequence[tuple[date, float]], day: date) -> Optional[float]:
    if not closes:
        return None
    days = [d for d, _ in closes]
    k = bisect_right(days, day)
    return closes[k - 1][1] if k else None


def _otm(price: Optional[float], strike: float) -> Optional[float]:
    return None if not price or price <= 0 else (price - strike) / price * 100


def evaluate_idea(
    idea: CommunityIdea, today: date, snapshots: Mapping[SNAPSHOT_KEY, ContractSnapshot], infos: Mapping[str, TickerInfo],
    closes: Mapping[str, Sequence[tuple[date, float]]], watchlist: set[str], mode: PriceReference, spread_pct: float,
    drop_warn_pct: float = 5.0, min_premium_ratio: float = 0.8, commission: Optional[CommissionModel] = None,
) -> IdeaView:
    """`commission`: tarifa de IBKR. La prima de hoy (`premium_now`), su yield y el descuento de hoy son NETOS de la comisión de apertura (un spread, una sola
    orden combo); la comparación con lo que cobró la idea (`LOWER`) se hace con la prima bruta, para no penalizarla dos veces."""
    dte = (idea.expiry - today).days
    base = idea.short_strike - idea.long_strike if idea.long_strike is not None else idea.short_strike
    yield_open = idea.credit / base * 100 if base > 0 else None
    info = infos.get(idea.ticker)
    price_now = info.underlying_price if info else None
    price_open = _close_on_or_before(closes.get(idea.ticker, ()), idea.day)
    move = (price_now / price_open - 1) * 100 if price_now and price_open else None
    common = dict(idea=idea, dte=dte, in_watchlist=idea.ticker in watchlist, price_open=price_open, price_now=price_now, move_pct=move,
                  otm_open_pct=_otm(price_open, idea.short_strike), otm_now_pct=_otm(price_now, idea.short_strike), yield_open_pct=yield_open,
                  iv_rank=info.iv_rank if info else None, iv_percentile=info.iv_percentile if info else None)
    if dte < 0:
        return IdeaView(status=EXPIRED, text="Vencida", **common)
    short = snapshots.get(snapshot_key(idea.ticker, idea.expiry, idea.short_strike))
    long_ = snapshots.get(snapshot_key(idea.ticker, idea.expiry, idea.long_strike)) if idea.long_strike is not None else None
    premium = None
    if short is not None:
        sell = reference_price(short.bid, short.ask, mode, spread_pct) if short.bid and short.bid > 0 else None
        if sell is not None and (idea.long_strike is None or (long_ is not None and long_.ask)):
            premium = sell - (long_.ask if long_ is not None else 0.0)
    gross = premium
    if premium is not None:
        if commission is not None:
            premium -= commission.per_share(short.contract.multiplier, 1 if idea.long_strike is None else 2)
            if price_now:
                common.update(otm_now_pct=(price_now - (idea.short_strike - premium)) / price_now * 100)     # descuento hasta el breakeven con la prima neta
        common.update(premium_now=premium, yield_now_pct=premium / base * 100 if base > 0 else None, delta_now=short.delta)
    if price_now is not None and price_now <= idea.short_strike:
        return IdeaView(status=ITM, text=f"En el dinero: el precio ({price_now:.2f}) ya está por debajo del strike vendido", **common)
    if move is not None and move <= -drop_warn_pct:
        return IdeaView(status=DROPPED, text=f"El subyacente ha caído {abs(move):.1f} % desde que se abrió: reevalúa antes de copiarla", **common)
    if premium is None:
        why = "sin cotización de esas patas" + ("" if idea.ticker in watchlist else " (no está en tu watchlist)")
        return IdeaView(status=NO_DATA, text=f"Sin valorar: {why}", **common)
    ratio = gross / idea.credit if idea.credit > 0 else None
    if ratio is not None and ratio < min_premium_ratio:
        return IdeaView(status=LOWER, text=f"Prima menor: hoy cobrarías el {ratio * 100:.0f} % de lo que cobró", **common)
    return IdeaView(status=VALID, text="Vigente: prima y distancia parecidas a las de la apertura", **common)


def evaluate_ideas(ideas: Sequence[CommunityIdea], today: date, snapshots, infos, closes, watchlist: set[str], mode: PriceReference,
                   spread_pct: float, **kw) -> list[IdeaView]:
    """Más recientes primero; a igualdad de día, las vigentes antes."""
    views = [evaluate_idea(i, today, snapshots, infos, closes, watchlist, mode, spread_pct, **kw) for i in ideas]
    order = {VALID: 0, LOWER: 1, NO_DATA: 2, DROPPED: 3, ITM: 4, EXPIRED: 5}
    return sorted(views, key=lambda v: (-v.idea.day.toordinal(), order[v.status], v.idea.ticker, v.idea.trader))


def past_trade(idea: CommunityIdea, closes: Sequence[Close], today: date, max_days: int) -> Optional[PastTrade]:
    """Tu cierre más reciente de una put vendida en el ticker de la idea, de hace como mucho `max_days` días (el del mismo contrato manda
    sobre uno más reciente de otro strike). `closes` ya viene con los más recientes primero."""
    ticker = norm_ticker(idea.ticker)
    best: Optional[PastTrade] = None
    for c in closes:
        age = (today - c.day).days
        if c.ticker != ticker or age < 0 or age > max_days:
            continue
        same = c.expiry == idea.expiry and abs(c.strike - idea.short_strike) < 1e-4
        if best is None or (same and not best.same_contract):
            best = PastTrade(c.day, age, c.pct_captured, c.pnl, same)
    return best
