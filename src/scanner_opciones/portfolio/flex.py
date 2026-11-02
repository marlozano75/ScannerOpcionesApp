"""Fecha exacta de apertura de las puts vendidas desde un IBKR Flex Query (informe de solo lectura; no envía órdenes ni toca la cuenta).

IBKR no da la fecha de apertura de una posición por la API de TWS. Un Flex Query configurado en IBKR (Client Portal → Informes → Flex Queries)
con la sección «Trades» (nivel Ejecuciones) la trae. El servicio web de Flex funciona en dos pasos: `SendRequest` (token + id de la consulta) devuelve un
código de referencia y `GetStatement` el informe en XML. Aquí se convierte en el mismo JSON de aperturas (versión 1) que lee `portfolio/openings.py`,
así que el resto de la aplicación no cambia: `AppService.openings` lo combina con el de Tortuga (gana la fecha más antigua del contrato).

Campos que debe llevar la sección Trades: assetCategory, underlyingSymbol (o symbol), expiry, strike, putCall, buySell, quantity, openCloseIndicator y
tradeDate (o dateTime). Para cada put se sigue la posición neta ejecución a ejecución: la fecha de apertura es la de la ejecución con la que la posición
actual salió de cero (si se cerró y se volvió a abrir, cuenta la última apertura). Funciones puras salvo `fetch_statement`."""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import httpx

BASE = "https://gdcdyn.interactivebrokers.com/Universal/servlet/"
RETRY_CODES = {"1001", "1018", "1019", "1021"}     # el informe aún no está listo / demasiadas peticiones: se reintenta
VERSION = 1


class FlexError(RuntimeError):
    """El servicio de Flex rechazó la petición o el informe no tiene el formato esperado."""


def _tag(xml: str, name: str) -> Optional[str]:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else None


async def fetch_statement(token: str, query_id: str, *, timeout: float = 60.0, attempts: int = 4, wait: float = 5.0,
                          client: Optional[httpx.AsyncClient] = None) -> str:
    """XML del informe. `FlexError` con el motivo de IBKR si falla; los errores transitorios (1001, 1019…) se reintentan con espera creciente."""
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        last = ""
        for i in range(attempts):
            if i:
                await asyncio.sleep(wait * i)
            sent = (await http.get(BASE + "FlexStatementService.SendRequest", params={"t": token, "q": query_id, "v": 3})).text
            if _tag(sent, "Status") != "Success":
                code, msg = _tag(sent, "ErrorCode") or "?", _tag(sent, "ErrorMessage") or sent[:200]
                last = f"{code}: {msg}"
                if code in RETRY_CODES:
                    continue
                raise FlexError(last)
            ref, url = _tag(sent, "ReferenceCode"), _tag(sent, "Url") or BASE + "FlexStatementService.GetStatement"
            for j in range(attempts):
                got = (await http.get(url, params={"t": token, "q": ref, "v": 3})).text
                if "<FlexQueryResponse" in got:
                    return got
                code = _tag(got, "ErrorCode") or "?"
                last = f"{code}: {_tag(got, 'ErrorMessage') or got[:200]}"
                if code not in RETRY_CODES:
                    raise FlexError(last)
                await asyncio.sleep(wait * (j + 1))
        raise FlexError(f"IBKR no entregó el informe tras {attempts} intentos ({last})")
    except httpx.HTTPError as exc:
        raise FlexError(f"no se pudo contactar con IBKR Flex: {exc}") from exc
    finally:
        if own:
            await http.aclose()


def _day(row: dict) -> Optional[date]:
    raw = (row.get("tradeDate") or row.get("dateTime") or "").strip()
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", raw)
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def _expiry(raw: str) -> Optional[date]:
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})$", (raw or "").strip())
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def statement_to_openings(xml: str, today: Optional[date] = None, now: Optional[datetime] = None) -> tuple[dict, list[str]]:
    """(JSON de aperturas versión 1, avisos) a partir del XML del informe. Solo puts con posición vendida abierta (vencimiento ≥ hoy)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FlexError(f"el informe no es un XML válido: {exc}") from exc
    rows = [dict(t.attrib) for t in root.iter("Trade")]
    if any(r.get("levelOfDetail") == "EXECUTION" for r in rows):      # si la consulta trae también órdenes o resúmenes, se cuentan solo las ejecuciones
        rows = [r for r in rows if r.get("levelOfDetail") == "EXECUTION"]
    else:
        rows = [r for r in rows if r.get("levelOfDetail") not in ("ORDER", "SUMMARY", "CLOSED_LOT")]
    warnings: list[str] = []
    today = today or date.today()
    puts: dict[tuple, list[tuple[datetime | date, float]]] = {}
    for i, r in enumerate(rows):
        if r.get("assetCategory") != "OPT" or r.get("putCall") != "P":
            continue
        try:
            ticker = (r.get("underlyingSymbol") or r.get("symbol", "").split(" ")[0]).strip()
            expiry, day = _expiry(r.get("expiry", "")), _day(r)
            strike, qty = float(r["strike"]), abs(float(r["quantity"]))
            side = {"BUY": 1, "SELL": -1}[r["buySell"].upper()]
            if not ticker or expiry is None or day is None or qty <= 0 or strike <= 0:
                raise ValueError("falta ticker, vencimiento, fecha, cantidad o strike")
        except (KeyError, ValueError) as exc:
            warnings.append(f"operación {i} descartada: {exc}")
            continue
        order = (r.get("dateTime") or day.isoformat())
        puts.setdefault((ticker, expiry, round(strike, 4)), []).append((day, order, side * qty))     # type: ignore[arg-type]
    out = []
    for (ticker, expiry, strike), trades in puts.items():
        if expiry < today:
            continue
        net, started = 0.0, None
        for day, _, signed in sorted(trades, key=lambda t: (t[0], t[1])):
            if abs(net) < 1e-9:
                started = day              # sale de cero: empieza una posición nueva
            net += signed
        if net < -1e-9 and started is not None:
            out.append({"date": started.isoformat(), "ticker": ticker, "expiry": expiry.isoformat(), "strike": strike, "qty": abs(net)})
    if not rows:
        warnings.append("el informe no trae ninguna operación (¿falta la sección Trades en la consulta?)")
    elif not puts:
        warnings.append("el informe no trae operaciones de puts (campos assetCategory y putCall)")
    return ({"version": VERSION, "generated_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"), "source": "ibkr-flex",
             "puts": sorted(out, key=lambda x: (x["date"], x["ticker"]))}, warnings)
