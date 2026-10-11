"""Última cuenta y posiciones leídas de TWS, guardadas para mostrarlas cuando no hay conexión (p. ej. al
arrancar la aplicación con TWS cerrado). Se guarda por modo de cuenta (real / simulada) para no mezclar cuentas."""
from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import date, datetime
from typing import Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, OptionContract, Position


def dump(mode: str, account: Optional[AccountSummary], positions: list[Position]) -> str:
    def default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, OptionRight):
            return o.value
        raise TypeError(type(o))
    return json.dumps(
        {"mode": mode, "account": asdict(account) if account else None, "positions": [asdict(p) for p in positions]},
        default=default,
    )


def load(raw: Optional[str], mode: str) -> tuple[Optional[AccountSummary], list[Position]]:
    """(cuenta, posiciones) guardadas para `mode`; vacío si no hay nada, no es de ese modo o está dañado."""
    if not raw:
        return None, []
    try:
        data = json.loads(raw)
        if data.get("mode") != mode:
            return None, []
        acc = data.get("account")
        account = None
        if acc:
            names = {f.name for f in fields(AccountSummary)}
            acc = {k: v for k, v in acc.items() if k in names}
            if acc.get("updated_at"):
                acc["updated_at"] = datetime.fromisoformat(acc["updated_at"])
            account = AccountSummary(**acc)
        positions = []
        for p in data.get("positions", []):
            opt = p.get("option")
            option = None
            if opt:
                option = OptionContract(
                    ticker=opt["ticker"], expiry=date.fromisoformat(opt["expiry"]), strike=opt["strike"],
                    right=OptionRight(opt["right"]), multiplier=opt.get("multiplier", 100), con_id=opt.get("con_id"))
            positions.append(Position(p["ticker"], p["quantity"], p["market_value"], p.get("sector"), option, p.get("avg_cost")))
        return account, positions
    except (ValueError, KeyError, TypeError):
        return None, []
