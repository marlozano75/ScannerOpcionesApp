"""Validacion fuera de muestra, paso 2: descarta los tickers sin 400 velas o cuyo cierre de Yahoo no coincide con daily_bars de la BD (simbolos distintos) -> $D/yf_val_ok.pkl.
Uso (desde la raiz del repo): python scripts/suelos/validacion_filtro.py $D"""
import pandas as pd, sqlite3, numpy as np
import sys; D=sys.argv[1]
Y=pd.read_pickle(D+'/yf_val.pkl'); c=sqlite3.connect('file:data/app.db?mode=ro',uri=True)
db=pd.read_sql("select ticker,day,close from daily_bars where day>='2023-06-01'",c); db['day']=pd.to_datetime(db.day)
db=db.pivot(index='day',columns='ticker',values='close')
tks=Y.columns.get_level_values(0).unique(); good=[];bad=[];nodata=[]
for t in tks:
    x=Y[t]['Close'].dropna()
    if len(x)<400: nodata.append(t); continue
    if t not in db.columns: bad.append((t,'sinDB')); continue
    j=pd.concat([x,db[t]],axis=1,keys=['y','d']).dropna()
    if len(j)<100: bad.append((t,'pocos')); continue
    r=j.y/j.d
    if 0.97<r.median()<1.03 and (abs(r-1)<0.03).mean()>0.9: good.append(t)
    else: bad.append((t,round(r.median(),2)))
print('tickers',len(tks),'buenos',len(good),'sin datos (<400 velas)',len(nodata),'discrepantes',len(bad)); print(bad[:12])
Y[[c for c in Y.columns if c[0] in set(good)]].to_pickle(D+'/yf_val_ok.pkl')
