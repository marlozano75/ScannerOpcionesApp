"""Criba teórica de tickers (informe, no filtra nada): ¿puede un ticker llegar alguna vez al yield mínimo de un perfil de puts?

Con el precio y la IV de la ficha se calcula, con Black-Scholes, la MEJOR prima posible dentro de la ventana del perfil (strike lo más cerca posible del
precio y el DTE máximo: el yield crece con el DTE y baja con el descuento) y se compara con el yield mínimo exigido. La estimación es generosa a propósito:
usa el mid (el escáner vende entre bid y mid), ignora comisiones, la delta y los demás filtros y, encima, se multiplica por `tolerance`, porque la prima real
se aparta de la teórica hasta ~2,8× según el ticker (sonrisa de volatilidad, ver docs/seleccion-tickers.md). Solo sirve para descartar extremos. Funciones puras."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.metrics.greeks import put_price
from scanner_opciones.scanner.criteria import ScanCriteria

TOLERANCE = 3.0          # factor sobre la prima teórica antes de descartar (la prima real llega a ~2,8× la teórica)

OK, DOUBTFUL, DISCARD, NO_DATA = "ok", "dudoso", "descartable", "sin dato"


@dataclass(frozen=True)
class ProfileScreen:
    profile: str
    required_pct: float          # yield mínimo del perfil (del periodo, no anualizado)
    best_pct: float              # mejor yield teórico de la ventana, sin tolerancia
    status: str

    @property
    def margin(self) -> float:
        """Mejor yield teórico / exigido (sin tolerancia): < 1 = ni la mejor prima teórica llega."""
        return self.best_pct / self.required_pct if self.required_pct > 0 else float("inf")


@dataclass(frozen=True)
class TickerScreen:
    ticker: str
    price: Optional[float]
    iv_pct: Optional[float]
    profiles: tuple[ProfileScreen, ...]
    status: str                  # estado del ticker: descartable solo si lo es para TODOS los perfiles de puts
    protected_by: tuple[str, ...] = ()   # modos (spread, CDS) que lo mantienen aunque no sirva para vender puts

    @property
    def best(self) -> Optional[ProfileScreen]:
        """El perfil al que más se acerca (mayor mejor-yield / exigido)."""
        return max(self.profiles, key=lambda p: p.margin) if self.profiles else None


def iv_used_pct(info: TickerInfo) -> Optional[float]:
    """IV (%) con la que se estima la prima: la mayor entre la IV a 30 días y la de cualquier vencimiento de tastytrade (estimación generosa)."""
    values = [info.iv30] if info.iv30 else []
    values += [v * 100 for v in (info.expiry_ivs or {}).values() if v and v > 0]
    return max(values) if values else None


def best_put_yield_pct(price: float, iv_pct: float, criteria: ScanCriteria) -> float:
    """Mejor yield teórico (mid / strike, en % del periodo) dentro de la ventana de `criteria`: strike al descuento mínimo y DTE máximo."""
    strike = price * (1 - criteria.strike_below_pct_min / 100)
    return put_price(price, strike, max(criteria.dte_max, 1) / 365, iv_pct / 100) / strike * 100


def screen_profile(name: str, price: float, iv_pct: float, criteria: ScanCriteria, tolerance: float = TOLERANCE) -> ProfileScreen:
    best = best_put_yield_pct(price, iv_pct, criteria)
    required = criteria.min_yield_pct
    status = OK if best >= required else DISCARD if best * tolerance < required else DOUBTFUL
    return ProfileScreen(name, required, best, status)


@dataclass(frozen=True)
class ProfileCount:
    profile: str
    required_pct: float
    evaluated: int       # tickers con precio e IV
    short: int           # no llegan al yield exigido ni con la mejor prima teórica
    hopeless: int        # ni multiplicando esa prima por la tolerancia


def profile_counts(infos: Sequence[TickerInfo], put_profiles: Sequence[tuple[str, ScanCriteria]], tolerance: float = TOLERANCE) -> list[ProfileCount]:
    """Por perfil de puts, cuántos tickers no podrían cumplir su yield mínimo (cada perfil por separado, sin la unión)."""
    out = []
    for name, c in put_profiles:
        n = short = hopeless = 0
        for info in infos:
            iv = iv_used_pct(info)
            if not info.underlying_price or info.underlying_price <= 0 or iv is None:
                continue
            n += 1
            best = best_put_yield_pct(info.underlying_price, iv, c)
            short += best < c.min_yield_pct
            hopeless += best * tolerance < c.min_yield_pct
        out.append(ProfileCount(name, c.min_yield_pct, n, short, hopeless))
    return out


def screen_ticker(ticker: str, info: Optional[TickerInfo], put_profiles: Sequence[tuple[str, ScanCriteria]], protected_by: Sequence[str] = (),
                  tolerance: float = TOLERANCE) -> TickerScreen:
    """Un ticker es DESCARTABLE solo si ningún perfil de puts lo puede cumplir ni con tolerancia; sin precio, sin IV o sin perfiles es «sin dato»."""
    price = info.underlying_price if info else None
    iv = iv_used_pct(info) if info else None
    if not price or price <= 0 or iv is None or not put_profiles:
        return TickerScreen(ticker, price, iv, (), NO_DATA, tuple(protected_by))
    rows = tuple(screen_profile(n, price, iv, c, tolerance) for n, c in put_profiles)
    if any(r.status == OK for r in rows):
        status = OK
    elif any(r.status == DOUBTFUL for r in rows):
        status = DOUBTFUL
    else:
        status = DISCARD
    return TickerScreen(ticker, price, iv, rows, status, tuple(protected_by))
