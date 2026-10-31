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
   - legs     = a short human string built from the "legs" array: for each leg write "ACTION QTY RIGHT" where RIGHT is the
                strike followed by C or P for options (e.g. "SELL 1 490P", "BUY 1 485P") or "acc" for a stock leg (right STK, e.g.
                "BUY 2000 acc"); join the legs with " / ". If the option legs share one expiry, append " · vence DD/MM" with that
                expiry. Keep it under ~90 characters.
   - note     = the trade "note" (the trader's own text) as a string, or null when it is null or empty.

OUTPUT FORMAT (version 1) - exactly these keys:
{
  "version": 1,
  "generated_at": "<today as YYYY-MM-DDT13:00:00+02:00 or +01:00, the Madrid offset of today>",
  "source": "tortuga",
  "trades": [
    {"id": "cb8a", "trader": "A.A.G", "ticker": "MSFT", "strategy": "Iron Condor", "status": "open",
     "opened": "2026-10-30", "closed": null, "pnl": null, "currency": "USD",
     "legs": "SELL 1 550C / BUY 1 555C / BUY 1 485P / SELL 1 490P · vence 06/11", "note": null},
    {"id": "7e30", "trader": "Kike", "ticker": "AMZN", "strategy": "Long Call", "status": "closed",
     "opened": "2026-10-30", "closed": "2026-10-30", "pnl": 83.42, "currency": "USD",
     "legs": "BUY 3 245C · vence 02/11", "note": "entro por el hueco"}
  ]
}
Rules: numbers are JSON numbers, dates are YYYY-MM-DD, "note" holds only the trader's own text (never invent one). If get_context or
list_community_trades fails, retry it once; if it still fails, print the single word FAILED and nothing else.
