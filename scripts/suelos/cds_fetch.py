"""R20: velas diarias de Yahoo 2004-2026 de 36 grandes empresas de calidad + SPY -> $D/q.pkl."""
import sys
import yfinance as yf
T = ('AAPL MSFT NVDA AMZN META GOOGL AVGO ADBE NOW MCD PEP KO JNJ PG V MA COST UNH HD LLY '
     'JPM BAC ORCL CRM TXN QCOM ABBV MRK WMT NFLX INTU ISRG AMD CAT DE GS').split()
d = yf.download(T + ['SPY'], start='2004-01-01', auto_adjust=False, actions=False,
                progress=False, group_by='ticker', threads=True)
d.to_pickle(sys.argv[1] + '/q.pkl')
