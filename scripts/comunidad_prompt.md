You are a data-extraction job. Build ONE JSON document from the Tortuga Trades community tools and print it as your final
answer. Print ONLY the JSON: no explanations, no markdown fences, nothing before or after it. You may NOT call any tool other
than get_context, get_community_screener and list_public_operations. Do not invent numbers: copy them from the tool results.

STEPS
1. Call get_context. Keep "today" (Europe/Madrid) for generated_at and the date ranges below.
2. Call get_community_screener with window="month", strategy="Short Put,Bull Put Spread", limit=100.
   Each item of its "tickers" list gives: key (ticker), count (operations), traders (number of traders behind it), faces
   (usernames of the traders that share).  month_operations = count, month_traders = traders, traders = faces.
3. Call get_community_screener again with window="week" and the same strategy and limit. For every ticker that also appears in
   the month list set week_operations = count and week_traders = traders (0 if it does not appear in the week list).
   Tickers that appear only in the week list are ignored.
4. Call list_public_operations with view="structures", side="open", strategy="Short Put,Bull Put Spread", limit=200,
   from = (today minus 4 days), to = today. If next_cursor is not null, repeat with the same filters and cursor=next_cursor,
   up to 3 pages in total.  Build "ideas" from the structures of every trader:
   - Short Put: exactly one leg, action SELL, right P  ->  strategy "Short Put", short_strike = leg strike, long_strike = null.
   - Bull Put Spread: exactly two legs, both right P, same expiry and same qty, one SELL (higher strike) and one BUY (lower
     strike)  ->  strategy "Bull Put Spread", short_strike = the SELL strike, long_strike = the BUY strike.
   - Anything else (calls, three or more legs, mixed expiries, different quantities, stock) is skipped.
   - contracts = qty of the SELL leg.  expiry = leg expiry.  date = the structure "day".  trader = the trader "username".
   - credit = premium.amount / (100 * contracts)  rounded to 4 decimals, only when premium.type is "credit"
     (skip the structure if it is a debit).  max_loss = the structure "max_loss" (null if absent).
   - Skip the underlyings SPX, SPXW, NDX, XSP, RUT, VIX and VIXW.
   - Keep at most 150 ideas, the most recent first.

OUTPUT FORMAT (version 1) - exactly these keys:
{
  "version": 1,
  "generated_at": "<today as YYYY-MM-DDT13:00:00+02:00 or +01:00, the Madrid offset of today>",
  "source": "tortuga",
  "tickers": [
    {"ticker": "PEP", "month_traders": 17, "month_operations": 45, "week_traders": 6, "week_operations": 10, "traders": ["JBJ", "yors37"]}
  ],
  "ideas": [
    {"date": "2026-10-09", "trader": "Juancm", "ticker": "VZ", "strategy": "Short Put", "expiry": "2026-11-20",
     "short_strike": 42, "long_strike": null, "contracts": 1, "credit": 0.96, "max_loss": 4104}
  ]
}
Rules: "tickers" has one entry per ticker of the month list (up to 100), ordered as the screener returns them. Numbers are JSON
numbers, dates are YYYY-MM-DD, "traders" holds only usernames the tool returned (never invent or guess names). If a tool fails,
retry it once; if it still fails, print the single word FAILED and nothing else.
