"""Carteras 13F de los Super Investors de Tortuga (`get_super_investors`): gestores de calidad y valor cuyas mayores posiciones sirven como
fuente de empresas de calidad para vender puts. Los 13F llegan con ~45 días de retraso y solo cubren posiciones largas en EE. UU.: es una
lista de empresas que se querrían tener, nunca una señal de entrada.

La aplicación no pide a Claude que copie nada: `build_investors_document` arma el JSON con el resultado BRUTO de cada llamada
(`ToolCall.output`) y `parse_investors` es lo que luego lee la aplicación (mismo código para validar y para leer)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

VERSION = 1
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,9}$")


class InvestorsError(ValueError):
    """El documento de Super Investors no es válido."""


@dataclass(frozen=True)
class Holding:
    ticker: str            # con el formato de la app («BRK-B»)
    issuer: str
    weight_pct: float
    status: str            # new, added, trimmed, held, exited


@dataclass(frozen=True)
class Manager:
    slug: str
    name: str
    fund: str
    quarter: str
    holdings: tuple[Holding, ...]


@dataclass(frozen=True)
class InvestorsData:
    generated_at: datetime
    managers: tuple[Manager, ...]


def app_ticker(symbol: object) -> Optional[str]:
    """Ticker de la app a partir del de Tortuga («BRK/B» -> «BRK-B»); None si no es un ticker utilizable."""
    text = str(symbol or "").strip().upper().replace("/", "-")
    return text if TICKER.match(text) else None


def _num(value: object) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def build_investors_document(calls: list, now: datetime, managers: list[str]) -> dict:
    """Documento de la versión 1 con las llamadas a `get_super_investors(investor=...)`: se exige que estén TODOS los gestores pedidos con al
    menos una posición (si falta uno el resultado sería engañoso: la fuente cambiaría sin avisar). Si un gestor viene paginado se juntan las páginas."""
    wanted = {m.lower() for m in managers}
    found: dict[str, dict] = {}
    for call in calls:
        args = getattr(call, "input", None) or {}
        if getattr(call, "name", "") != "get_super_investors" or not args.get("investor"):
            continue
        try:
            result = json.loads(call.output)
        except ValueError:
            continue
        slug = str(result.get("slug", "")).lower() if isinstance(result, dict) else ""
        if slug not in wanted or not isinstance(result.get("positions"), list):
            continue
        entry = found.setdefault(slug, {"slug": slug, "name": result.get("name", ""), "fund": result.get("fund", ""),
                                        "quarter": result.get("latest_quarter", ""), "positions": {}})
        for item in result["positions"]:
            ticker = app_ticker(item.get("ticker")) if isinstance(item, dict) else None
            if ticker and _num(item.get("weight_pct")) is not None:
                entry["positions"].setdefault(ticker, {"ticker": ticker, "issuer": str(item.get("issuer", "")),
                                                       "weight_pct": _num(item["weight_pct"]), "status": str(item.get("status", ""))})
    missing = sorted(wanted - {s for s, e in found.items() if e["positions"]})
    if missing:
        raise InvestorsError("faltan gestores en las respuestas: " + ", ".join(missing))
    out = [{**e, "positions": sorted(e["positions"].values(), key=lambda p: -p["weight_pct"])} for e in found.values()]
    return {"version": VERSION, "generated_at": now.astimezone().isoformat(timespec="seconds"), "source": "tortuga-super-investors",
            "managers": sorted(out, key=lambda m: m["slug"])}


def parse_investors(doc: object) -> InvestorsData:
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError as exc:
            raise InvestorsError(f"no es un JSON válido: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("managers"), list):
        raise InvestorsError("formato no reconocido (versión 1 con «managers»)")
    try:
        generated = datetime.fromisoformat(str(doc.get("generated_at")))
    except ValueError as exc:
        raise InvestorsError("generated_at no es una fecha") from exc
    managers = []
    for m in doc["managers"]:
        if not isinstance(m, dict) or not m.get("slug") or not isinstance(m.get("positions"), list):
            raise InvestorsError("gestor mal formado")
        holdings = tuple(Holding(p["ticker"], str(p.get("issuer", "")), float(p["weight_pct"]), str(p.get("status", "")))
                         for p in m["positions"] if isinstance(p, dict) and app_ticker(p.get("ticker")) and _num(p.get("weight_pct")) is not None)
        if not holdings:
            raise InvestorsError(f"el gestor {m.get('slug')} no tiene posiciones")
        managers.append(Manager(str(m["slug"]), str(m.get("name", "")), str(m.get("fund", "")), str(m.get("quarter", "")), holdings))
    if not managers:
        raise InvestorsError("no hay ningún gestor")
    return InvestorsData(generated, tuple(managers))


def load_investors(path: Path) -> InvestorsData:
    try:
        return parse_investors(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvestorsError(str(exc)) from exc


def quality_holdings(data: InvestorsData, min_weight_pct: float, min_managers: int = 1) -> dict[str, dict]:
    """{ticker: {managers: [nombres], max_weight: %}} de las posiciones con al menos `min_weight_pct` de la cartera (y no vendidas del todo)
    en `min_managers` o más gestores. Ordenado por nº de gestores y peso máximo."""
    acc: dict[str, dict] = {}
    for m in data.managers:
        for h in m.holdings:
            if h.weight_pct >= min_weight_pct and h.status != "exited":
                e = acc.setdefault(h.ticker, {"managers": [], "max_weight": 0.0})
                e["managers"].append(m.name or m.slug)
                e["max_weight"] = max(e["max_weight"], h.weight_pct)
    kept = {t: e for t, e in acc.items() if len(e["managers"]) >= min_managers}
    return dict(sorted(kept.items(), key=lambda kv: (-len(kv[1]["managers"]), -kv[1]["max_weight"], kv[0])))
