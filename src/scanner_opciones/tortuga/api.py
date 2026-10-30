"""Cliente de la API de Tortuga Trades (`https://tortugatrades.app/api/v1/<herramienta>`, solo lectura, `Authorization: Bearer tt_live_…`).

Devuelve lo mismo que el conector que leía la CLI de Claude, así que las rutinas con `tortuga.api_token` obtienen las `ToolCall` (herramienta,
argumentos, resultado bruto) directamente y siguen validándose con el mismo código. La API NO tiene las herramientas de comunidad
(`get_community_screener`, `list_public_operations`): esa rutina sigue usando la CLI de Claude."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from scanner_opciones.tortuga.claude_runner import ToolCall
from scanner_opciones.tortuga.radar import STRATEGIES

BASE_URL = "https://tortugatrades.app/api/v1"
TIMEOUT_SECONDS = 30
RETRIES = 2
RADAR_LIMIT = 100
PRICE_RANGES = ((0, 50), (50, 100), (100, 200), (200, None))
SPLIT_OPEN_AT = 350                       # el tramo abierto «200+» se parte en 200-350 y 350+
MAX_SPLIT_DEPTH = 6
CARD_CONCURRENCY = 5                      # fichas por valor a la vez
API_ROUTINES = frozenset({"aperturas", "radar_spreads", "super_investors"})


class TortugaApiError(RuntimeError):
    """La API de Tortuga no respondió o devolvió un error."""


class TortugaApi:
    def __init__(self, token: str, base_url: str = BASE_URL, client: Optional[httpx.AsyncClient] = None) -> None:
        if not token:
            raise TortugaApiError("falta tortuga.api_token en config.yaml")
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._base, self._client = base_url.rstrip("/"), client

    async def call(self, name: str, **params) -> ToolCall:
        """GET de una herramienta (los parámetros a None no se envían); reintenta los fallos pasajeros."""
        args = {k: v for k, v in params.items() if v is not None}
        last = ""
        for attempt in range(RETRIES + 1):
            try:
                if self._client is not None:
                    resp = await self._client.get(f"{self._base}/{name}", params=args, headers=self._headers, timeout=TIMEOUT_SECONDS)
                else:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(f"{self._base}/{name}", params=args, headers=self._headers, timeout=TIMEOUT_SECONDS)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    return ToolCall(name, args, resp.text)
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code in (400, 401, 403, 404):          # no mejora reintentando
                    break
            await asyncio.sleep(1 + attempt)
        raise TortugaApiError(f"{name}: {last}")

    # ---- radar -------------------------------------------------------------------------------
    async def radar_calls(self) -> list[ToolCall]:
        """Por estrategia: una llamada sin filtros (da el total `matched`) y, si viene recortada, los tramos de precio, que se parten por la
        mitad mientras sigan recortados (el mismo procedimiento que seguía `scripts/radar_spreads_prompt.md`)."""
        groups = await asyncio.gather(*(self._radar_strategy(s) for s in STRATEGIES))
        calls = [c for group in groups for c in group]
        return calls + await self._spread_cards(calls)

    async def _spread_cards(self, calls: list[ToolCall]) -> list[ToolCall]:
        """Ficha de cada valor cuya estrategia propuesta es Bull Put Spread: la lista no trae los strikes del spread, la ficha sí (`idea`).
        Un fallo no invalida el radar: ese valor se queda sin crédito/ancho."""
        symbols: dict[str, None] = {}
        for call in calls:
            try:
                rows = json.loads(call.output).get("rows", [])
            except (ValueError, AttributeError):
                continue
            for r in rows:
                if isinstance(r, dict) and r.get("symbol") and "bullputspread" in str((r.get("top_strategy") or {}).get("key", "")).lower():
                    symbols[str(r["symbol"])] = None
        gate = asyncio.Semaphore(CARD_CONCURRENCY)

        async def card(sym: str) -> Optional[ToolCall]:
            async with gate:
                try:
                    return await self.call("get_market_radar", symbol=sym)
                except TortugaApiError:
                    return None
        return [c for c in await asyncio.gather(*(card(s) for s in symbols)) if c is not None]

    async def _radar_strategy(self, strategy: str) -> list[ToolCall]:
        first = await self.call("get_market_radar", strategy=strategy, sort="iv_rank", limit=RADAR_LIMIT)
        calls = [first]
        if not _cut(first):
            return calls
        pending = [(lo, hi, 0) for lo, hi in PRICE_RANGES]
        while pending:
            batch, pending = pending, []
            done = await asyncio.gather(*(self._radar_range(strategy, lo, hi) for lo, hi, _ in batch))
            for (lo, hi, depth), call in zip(batch, done):
                calls.append(call)
                if _cut(call):
                    if depth >= MAX_SPLIT_DEPTH:
                        raise TortugaApiError(f"radar ({strategy}): el tramo de precio {lo}-{hi} sigue recortado")
                    pending.extend((a, b, depth + 1) for a, b in _halves(lo, hi))
        return calls

    async def _radar_range(self, strategy: str, lo: float, hi: Optional[float]) -> ToolCall:
        return await self.call("get_market_radar", strategy=strategy, sort="iv_rank", limit=RADAR_LIMIT,
                               price_min=lo if lo else None, price_max=hi)

    # ---- super investors ---------------------------------------------------------------------
    async def investor_calls(self, managers: list[str]) -> list[ToolCall]:
        return list(await asyncio.gather(*(self.call("get_super_investors", investor=m, limit=50) for m in managers)))

    # ---- aperturas y cierres propios -----------------------------------------------------------
    async def openings_document(self, now: datetime) -> str:
        """El JSON de `aperturas.json` (versión 1): puts vendidas abiertas en los últimos 120 días y puts vendidas recompradas en los últimos 90."""
        ctx = json.loads((await self.call("get_context")).output)
        today = date.fromisoformat(ctx["today"])
        accounts = [a["account"] for a in ctx.get("accounts", [])]
        if not accounts:
            raise TortugaApiError("get_context no devuelve ninguna cuenta")
        puts, closes = [], []
        for account in accounts:
            for row in await self._trades(account, "open", today - timedelta(days=120), today, "date_asc"):
                if row.get("side") == "SELL" and row.get("right") == "P":
                    puts.append({"date": row["date"], "ticker": row["underlying"], "expiry": row["expiry"], "strike": row["strike"], "qty": row["qty"]})
            for row in await self._trades(account, "closed", today - timedelta(days=90), today, "date_desc"):
                if row.get("side") == "BUY" and row.get("right") == "P":
                    closes.append({"date": row["date"], "ticker": row["underlying"], "expiry": row["expiry"], "strike": row["strike"],
                                   "qty": row["qty"], "pct_captured": row.get("pct_premium_captured"), "pnl": row.get("pnl")})
        stamp = datetime.combine(today, datetime.min.time()).replace(hour=13, tzinfo=now.astimezone().tzinfo)
        return json.dumps({"version": 1, "generated_at": stamp.isoformat(timespec="seconds"), "source": "tortuga", "puts": puts, "closes": closes},
                          ensure_ascii=False)

    async def _trades(self, account: int, status: str, start: date, end: date, order: str, max_pages: int = 5) -> list[dict]:
        rows: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            call = await self.call("list_trades", account=account, status=status, type="option", sort=order, limit=200,
                                   **{"from": start.isoformat(), "to": end.isoformat(), "cursor": cursor})
            page = json.loads(call.output)
            rows.extend(r for r in page.get("trades", []) if isinstance(r, dict))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows


def _cut(call: ToolCall) -> bool:
    """¿La respuesta está recortada? (hay más coincidencias que filas devueltas)."""
    try:
        result = json.loads(call.output)
        return int(result.get("matched", 0)) > len(result.get("rows", []))
    except (ValueError, TypeError, AttributeError):
        return False


def _halves(lo: float, hi: Optional[float]) -> list[tuple[float, Optional[float]]]:
    if hi is None:
        return [(lo, SPLIT_OPEN_AT), (SPLIT_OPEN_AT, None)] if lo < SPLIT_OPEN_AT else [(lo, lo * 2), (lo * 2, None)]
    mid = round((lo + hi) / 2, 2)
    return [(lo, mid), (mid, hi)]
