"""Validacion fuera de muestra (2026-11-09), paso 1: descarga de Yahoo (2004-2026) de los tickers de la watchlist (data/app.db, solo lectura) que no estan en los 27 originales -> $D/yf_val.pkl.
Uso (desde la raiz del repo): python scripts/suelos/validacion_fetch.py $D"""
import sqlite3, re, sys, pandas as pd, yfinance as yf
sp=sys.argv[1]
T27=set('SPY QQQ IWM AAPL MSFT NVDA AMZN META GOOGL TSLA AMD NFLX JPM BAC XOM INTC MU PLTR COIN SOFI UBER DIS BA XLF GLD TLT SMH'.split())
c=sqlite3.connect('file:data/app.db?mode=ro',uri=True)
tk=[r[0] for r in c.execute("select ticker from watchlist order by ticker")]
us=[t for t in tk if re.fullmatch(r'[A-Z]{1,5}',t) and t not in T27]
print('watchlist',len(tk),'candidatos',len(us),flush=True)
parts=[]
for i in range(0,len(us),100):
    ch=us[i:i+100]
    try:
        d=yf.download(ch,start='2004-01-01',auto_adjust=False,actions=False,progress=False,group_by='ticker',threads=True)
        parts.append(d)
    except Exception as e: print('fallo lote',i,e,flush=True)
    print(i+len(ch),flush=True)
d=pd.concat(parts,axis=1); d=d.loc[:,~d.columns.duplicated()]
d.to_pickle(sp+'/yf_val.pkl'); print('listo',d.shape,flush=True)
