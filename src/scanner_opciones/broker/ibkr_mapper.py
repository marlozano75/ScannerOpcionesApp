"""Traducción de tipos IBKR -> dominio. Funciones puras (testeables sin TWS).

Recibe objetos "con forma" de ib_async (duck typing), no importa ib_async.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional, Sequence

from scanner_opciones.config.settings import AccountTags
from scanner_opciones.domain.models import AccountSummary


def num(x: Any) -> Optional[float]:
    """None para None/NaN/-1 (IBKR usa -1 y NaN para 'sin dato')."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f == -1:
        return None
    return f


def ib_symbol(ticker: str) -> str:
    """Símbolo en la forma de IBKR: las clases de acciones llevan un espacio, no punto ni guion
    («BRK.B» y «BRK-B» -> «BRK B»; «PBR-A» -> «PBR A»). Idempotente."""
    return re.sub(r"[.\-]", " ", ticker.strip())


def parse_expiry(text: str) -> date:
    """'20261030' -> date. (IBKR también puede dar 'YYYYMM' en futuros: se rechaza aquí)."""
    return datetime.strptime(text, "%Y%m%d").date()


def format_expiry(d: date) -> str:
    return d.strftime("%Y%m%d")


def pick_account_value(values: Iterable[Any], tag: str, account: Optional[str] = None) -> Optional[float]:
    """Busca `tag` (o `tag-S`/`tag-C`, segmentos) entre los account values.

    Prefiere la moneda 'BASE'; si no existe, la primera con valor numérico.
    """
    candidates = [tag, f"{tag}-S", f"{tag}-C"]
    for name in candidates:
        matches = [v for v in values if v.tag == name and (account is None or v.account == account)]
        if not matches:
            continue
        matches.sort(key=lambda v: 0 if getattr(v, "currency", "") == "BASE" else 1)
        for v in matches:
            n = num(v.value)
            if n is not None:
                return n
    return None


def account_fx(values: Sequence[Any], account: Optional[str] = None) -> tuple[Optional[str], Optional[float]]:
    """(moneda base, dólares por unidad de moneda base). La base es la moneda de NetLiquidation que no es «BASE»;
    el tag `ExchangeRate` de la moneda USD da las unidades de base por dólar. No verificado contra TWS."""
    mine = [v for v in values if account is None or v.account == account]
    base = next((str(v.currency) for v in mine if v.tag == "NetLiquidation" and getattr(v, "currency", "") not in ("", "BASE")), None)
    if base is None:
        return None, None
    if base == "USD":
        return base, 1.0
    # IB Gateway (medido 2026-10-29) no manda `ExchangeRate` para USD, sino `$LEDGER-ExchangeRate` (mismo significado)
    rate = next((num(v.value) for tag in ("ExchangeRate", "$LEDGER-ExchangeRate") for v in mine
                 if v.tag == tag and getattr(v, "currency", "") == "USD" and num(v.value)), None)
    return base, (1.0 / rate if rate and rate > 0 else None)


def build_account_summary(
    values: Sequence[Any], tags: AccountTags, account_id: str, now: datetime
) -> AccountSummary:
    def get(tag: str) -> Optional[float]:
        return pick_account_value(values, tag, account_id)

    alias = next((str(v.value) for v in values if v.tag == "AccountOrGroup" and getattr(v, "account", account_id) == account_id
                  and v.value and v.value != account_id), None)
    cushion = get(tags.cushion)
    severity = get(tags.highest_severity)
    base_currency, usd_per_base = account_fx(values, account_id)
    return AccountSummary(
        account_id=account_id,
        net_liquidation=get(tags.net_liquidation),
        excess_liquidity=get(tags.excess_liquidity),
        cushion_pct=cushion * 100 if cushion is not None else None,
        look_ahead_excess=get(tags.look_ahead_excess),
        post_expiration_excess=get(tags.post_expiration_excess),
        highest_severity=int(severity) if severity is not None else None,
        gross_position_value=get(tags.gross_position_value),
        updated_at=now,
        alias=alias, base_currency=base_currency, usd_per_base=usd_per_base,
    )


def pick_chain(chains: Sequence[Any], ticker: str, exchange: str = "SMART", trading_class: Optional[str] = None, multiplier: int = 100) -> Optional[Any]:
    """Elige la cadena de `exchange` (SMART en las acciones) con la clase de opciones `trading_class` (por defecto, el ticker) y el multiplicador
    `multiplier` (o la mejor disponible)."""
    wanted = trading_class or ticker

    def score(c) -> tuple:
        return (
            c.exchange == exchange,
            c.tradingClass == wanted,
            str(c.multiplier) == str(multiplier),
            len(c.expirations),
        )

    return max(chains, key=score) if chains else None


def parse_margin_change(text: Any) -> Optional[float]:
    """initMarginChange llega como string ('1234.5'); vacío o valor 'máximo' = sin dato."""
    n = num(text)
    if n is None or n >= 1e300:
        return None
    return n
