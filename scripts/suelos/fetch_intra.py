import yfinance as yf, pandas as pd, sys
T='SPY QQQ IWM AAPL MSFT NVDA AMZN META GOOGL TSLA AMD NFLX JPM BAC XOM INTC MU PLTR COIN SOFI UBER DIS BA XLF GLD TLT SMH'.split()
for iv,per,name in (('1h','730d','h1'),('5m','60d','m5')):
    d=yf.download(T,period=per,interval=iv,auto_adjust=False,progress=False,group_by='ticker',threads=True)
    d.to_pickle(f'{sys.argv[1]}/intra_{name}.pkl'); print(iv,d.shape,d.index.min(),d.index.max())
