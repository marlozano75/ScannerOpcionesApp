You are a data-extraction job. Build ONE JSON document from the Tortuga Trades community feed and print it as your final answer.
Print ONLY the JSON: no explanations, no markdown fences, nothing before or after it. You may NOT call any tool other than
get_context and list_community_trades. Do not invent numbers: copy them from the tool results.

STEPS
1. Call get_context. Keep "today" (Europe/Madrid) for generated_at.
2. Call list_community_trades. If the result has viewer_copy_trade_off = true, the user cannot see other traders' operations:
   print the single word FAILED and nothing else. Otherwise read its "trades" list. If "next_cursor" is not null, call
   list_community_trades again with cursor = next_cursor, up to 4 pages in total. Keep at most 80 trades, the most recent first
   (the feed is already ordered newest first). Ignore the "live" block.
3. For every trade build one object:
   - id       = the trade "id".
   - trader   = author.username  (skip the trade if it has no username).
   - ticker   = the "underlying".
   - strategy = the "strategy".
   - status   = "open" or "closed" (the trade "status").
   - opened   = the date part (YYYY-MM-DD) of opened_at.
   - closed   = the date part of closed_at, or null if it is null.
   - pnl       = realized_pnl when status is "closed" (a JSON number), else null.
   - currency = the trade "currency".
   - legs      = the "legs" array, one object per leg with EXACTLY these keys, copied from the tool result:
                 {"action": "BUY"|"SELL", "right": "C"|"P"|"STK", "strike": <number or null for stock>,
                  "expiry": "YYYY-MM-DD" or null for stock, "qty": <number>, "price": <number, the opening price>}.
   - close_legs = when status is "closed", the "close_legs" array as objects {"qty": <number>, "price": <number>} IN THE SAME ORDER
                  as "legs" (close_legs[i] is how leg i was closed). Use null when status is "open" or the tool gives no close_legs.
   - note     = the trade "note" (the trader's own text) as a string, or null when it is null or empty.

OUTPUT FORMAT (version 2) - exactly these keys:
{
  "version": 2,
  "generated_at": "<today as YYYY-MM-DDT13:00:00+02:00 or +01:00, the Madrid offset of today>",
  "source": "tortuga",
  "trades": [
    {"id": "cb8a", "trader": "A.A.G", "ticker": "MSFT", "strategy": "Iron Condor", "status": "open",
     "opened": "2026-10-30", "closed": null, "pnl": null, "currency": "USD", "note": null,
     "legs": [{"action": "SELL", "right": "C", "strike": 550, "expiry": "2026-11-06", "qty": 1, "price": 0.81},
              {"action": "BUY", "right": "C", "strike": 555, "expiry": "2026-11-06", "qty": 1, "price": 0.56},
              {"action": "BUY", "right": "P", "strike": 485, "expiry": "2026-11-06", "qty": 1, "price": 0.71},
              {"action": "SELL", "right": "P", "strike": 490, "expiry": "2026-11-06", "qty": 1, "price": 1.01}],
     "close_legs": null},
    {"id": "7e30", "trader": "Kike", "ticker": "AMZN", "strategy": "Long Call", "status": "closed",
     "opened": "2026-10-30", "closed": "2026-10-30", "pnl": 83.42, "currency": "USD", "note": "entro por el hueco",
     "legs": [{"action": "BUY", "right": "C", "strike": 245, "expiry": "2026-11-02", "qty": 3, "price": 0.8}],
     "close_legs": [{"qty": 3, "price": 1.09}]}
  ]
}
Rules: numbers are JSON numbers, dates are YYYY-MM-DD, copy every price from the tool result (never invent one), "note" holds only
the trader's own text. A stock leg has "right":"STK", "strike":null, "expiry":null. If get_context or list_community_trades fails,
retry it once; if it still fails, print the single word FAILED and nothing else.
