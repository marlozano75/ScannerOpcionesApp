"""Última cuenta y posiciones leídas de TWS, guardadas para mostrarlas cuando no hay conexión (p. ej. al
arrancar la aplicación con TWS cerrado). Se guarda por modo de cuenta (real / simulada) para no mezclar cuentas."""
from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import date, datetime
from typing import Optional

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, OptionContract, Position


def _modes(raw: Optional[str]) -> dict:
    """Entradas guardadas por modo de cuenta; entiende también el formato antiguo (un solo modo en la raíz)."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    if "modes" in data:
        return data["modes"] if isinstance(data["modes"], dict) else {}
    if data.get("mode"):
        return {data["mode"]: {"account": data.get("account"), "positions": data.get("positions", [])}}
    return {}


def dump(mode: str, account: Optional[AccountSummary], positions: list[Position], previous: Optional[str] = None) -> str:
    """JSON con la cuenta y posiciones de `mode`; conserva las de los demás modos que haya en `previous`."""
    def default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, OptionRight):
            return o.value
        raise TypeError(type(o))
    modes = _modes(previous)
    modes[mode] = {"account": asdict(account) if account else None, "positions": [asdict(p) for p in positions]}
    return json.dumps({"modes": modes}, default=default)


def load(raw: Optional[str], mode: str) -> tuple[Optional[AccountSummary], list[Position]]:
    """(cuenta, posiciones) guardadas para `mode`; vacío si no hay nada, no es de ese modo o está dañado."""
    try:
        data = _modes(raw).get(mode)
        if not data:
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
