import yfinance as yf, pandas as pd, sys
T='SPY QQQ IWM AAPL MSFT NVDA AMZN META GOOGL TSLA AMD NFLX JPM BAC XOM INTC MU PLTR COIN SOFI UBER DIS BA XLF GLD TLT SMH'.split()
M=['^VIX','^VIX3M','^VIX9D','^VVIX','^SKEW']
d=yf.download(T+M,start='2004-01-01',auto_adjust=False,actions=False,progress=False,group_by='ticker',threads=True)
d.to_pickle(sys.argv[1]+'/yf.pkl')
for t in T+M:
    x=d[t].dropna(subset=['Close']); print(t,len(x),x.index.min().date(),x.index.max().date(),'vol' if x['Volume'].fillna(0).sum()>0 else 'novol')
