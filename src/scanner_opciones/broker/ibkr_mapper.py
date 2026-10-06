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


def build_account_summary(
    values: Sequence[Any], tags: AccountTags, account_id: str, now: datetime
) -> AccountSummary:
    def get(tag: str) -> Optional[float]:
        return pick_account_value(values, tag, account_id)

    cushion = get(tags.cushion)
    severity = get(tags.highest_severity)
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
    )


def pick_chain(chains: Sequence[Any], ticker: str) -> Optional[Any]:
    """Elige la cadena SMART con tradingClass == ticker y multiplicador 100 (o la mejor disponible)."""
    def score(c) -> tuple:
        return (
            c.exchange == "SMART",
            c.tradingClass == ticker,
            str(c.multiplier) == "100",
            len(c.expirations),
        )

    return max(chains, key=score) if chains else None


def parse_margin_change(text: Any) -> Optional[float]:
    """initMarginChange llega como string ('1234.5'); vacío o valor 'máximo' = sin dato."""
    n = num(text)
    if n is None or n >= 1e300:
        return None
    return n
