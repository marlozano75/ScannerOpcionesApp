You are a data-extraction job. Build ONE JSON document from the Tortuga Trades Market Radar and print it as your final answer.
Print ONLY the JSON: no explanations, no markdown fences, nothing before or after it. You may NOT call any tool other than
get_context and get_market_radar. Do not invent numbers: copy them from the tool results.

STEPS
1. Call get_context. Keep "today" (Europe/Madrid) for generated_at.
2. Call get_market_radar ONCE with liquid_only=true, price_min=30, vrp_min=0, sort="put30_yield", limit=100 (no other filter).
   Every row is an underlying with options liquidity grade 3 or 4 and a positive VRP (IV30 minus RV20).
3. Build one entry per row (use null when the field is absent):
   - ticker: the "symbol", upper case.
   - sector: the "sector" exactly as returned (GICS sector in English, or "ETF").
   - liquidity: liquidity.stars as a number.
   - price: "price".
   - is_etf: true when the sector is "ETF", otherwise false.
   - vrp_pp: "vrp_pp".
   - put30_yield_pct: put30.annualized_yield_pct (null if put30 is absent).
   - trend: "trend" (bullish, neutral or bearish).
   - expected_move_pct: "expected_move_30d_pct".
   - iv30_pct: "iv30_pct".

OUTPUT FORMAT (version 1) - exactly these keys:
{
  "version": 1,
  "generated_at": "<today as YYYY-MM-DDT13:00:00+02:00 or +01:00, the Madrid offset of today>",
  "source": "tortuga-market-radar",
  "rows": [
    {"ticker": "AAPL", "sector": "Information Technology", "liquidity": 4, "price": 347.65, "is_etf": false,
     "vrp_pp": 10.4, "put30_yield_pct": 25, "trend": "bullish", "expected_move_pct": 8.2, "iv30_pct": 26.6}
  ]
}
Rules: keep the order the tool returns. Numbers are JSON numbers. Do not filter, rank or drop rows yourself and do not complete the
list with other tools. If the tool fails, retry it once; if it still fails, print the single word FAILED and nothing else.
