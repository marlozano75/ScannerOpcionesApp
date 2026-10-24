"""Cliente de la API de Tortuga: tramos de precio del radar y documento de aperturas (sin red: transporte simulado)."""
import json
from datetime import datetime

import httpx

from scanner_opciones.tortuga.api import TortugaApi
from scanner_opciones.tortuga.radar import build_radar_document


def _row(sym, price):
    return {"symbol": sym, "price": price, "iv_rank": 50}


def _handler(request: httpx.Request) -> httpx.Response:
    q, path = dict(request.url.params), request.url.path
    assert request.headers["authorization"] == "Bearer tt_live_x"
    if path.endswith("/get_market_radar"):
        universe = [_row("A", 10), _row("B", 70), _row("C", 150), _row("D", 250)]
        lo, hi = float(q.get("price_min", 0)), float(q.get("price_max", 1e9))
        rows = [r for r in universe if lo <= r["price"] < hi]
        if not q.get("price_min") and not q.get("price_max"):
            return httpx.Response(200, json={"as_of": "2026-10-23", "matched": 4, "rows": rows[:2]})     # recortada
        return httpx.Response(200, json={"as_of": "2026-10-23", "matched": len(rows), "rows": rows})
    if path.endswith("/get_context"):
        return httpx.Response(200, json={"today": "2026-10-24", "accounts": [{"account": 1}]})
    if path.endswith("/list_trades"):
        sold = {"date": "2026-10-13", "underlying": "FRO", "expiry": "2026-11-20", "strike": 49.2, "qty": 2, "right": "P"}
        rows = [{**sold, "side": "SELL"}, {**sold, "side": "BUY", "right": "C"}] if q["status"] == "open" else \
               [{**sold, "side": "BUY", "pct_premium_captured": 62.2, "pnl": 24.5}]
        return httpx.Response(200, json={"trades": rows, "next_cursor": None})
    return httpx.Response(404, json={"error": "unknown_tool"})


def _api():
    return TortugaApi("tt_live_x", client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)))


async def test_radar_splits_cut_responses_into_price_ranges():
    calls = await _api().radar_calls()
    doc = build_radar_document(calls, datetime(2026, 10, 24, 12))
    assert sorted(r["symbol"] for r in doc["rows"]) == ["A", "B", "C", "D"]


async def test_openings_document_keeps_only_short_puts():
    doc = json.loads(await _api().openings_document(datetime(2026, 10, 24, 12)))
    assert [p["ticker"] for p in doc["puts"]] == ["FRO"] and doc["puts"][0]["qty"] == 2
    assert doc["closes"][0]["pct_captured"] == 62.2
