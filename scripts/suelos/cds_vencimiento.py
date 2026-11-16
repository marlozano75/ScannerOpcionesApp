"""R20: probabilidad de que el subyacente NO haya subido +X % al vencer (CDS mantenido), por plazo; usa $D/q.pkl."""
import sys; sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd, numpy as np, sys
d=pd.read_pickle(sys.argv[1]+'/q.pkl')
T=[t for t in d.columns.levels[0] if t!='SPY']
spy=d['SPY']['Close'].dropna(); spy_up=(spy>spy.rolling(200).mean())
H={'200 d (140 ses.)':140,'280 d (195 ses.)':195,'365 d (252 ses.)':252,'500 d (345 ses.)':345}
TH=[0,10,25,40,60,100]
rows=[]
for t in T:
    c=d[t]['Close'].dropna()
    if len(c)<600: continue
    s200=c.rolling(200).mean(); s50=c.rolling(50).mean()
    up=(c>s200)&(s50>s200)&(s200>s200.shift(20))
    pull=up&(c<c.rolling(20).max()*0.93)   # alcista con retroceso >=7% desde el máximo de 20 ses.
    for lab,h in H.items():
        f=c.shift(-h)/c-1
        mn=c[::-1].rolling(h,min_periods=1).min()[::-1].shift(-1)/c-1  # mínimo del camino
        df=pd.DataFrame({'t':t,'h':lab,'r':f,'mn':mn,'up':up,'pull':pull,'spy':spy_up.reindex(c.index)}).dropna(subset=['r'])
        rows.append(df)
A=pd.concat(rows); A['yr']=A.index.year
def tab(m,name):
    out=[]
    for lab in H:
        x=A[m&(A.h==lab)]
        r=x.r*100
        o={'grupo':name,'plazo':lab,'n':len(x),'mediana %':r.median()}
        for k in TH: o[f'P(<+{k}%)']=(r<k).mean()*100
        o['P(<-20%)']=(r<-20).mean()*100
        out.append(o)
    return pd.DataFrame(out)
pd.set_option('display.width',250)
res=pd.concat([tab(A.up==A.up,'todos los días'),tab(A.up,'alcista'),tab(A.pull,'alcista + retroceso ≥7%'),tab(A.up&(A.index<'2015'),'alcista 2004-2014'),tab(A.up&(A.index>='2015'),'alcista 2015-2025')])
print(res.round(1).to_string(index=False))
# por año de entrada, 365 d, alcista
x=A[A.up&(A.h=='365 d (252 ses.)')]
g=x.groupby('yr').r.apply(lambda r: pd.Series({'P(<+25%)':(r<0.25).mean()*100,'P(<0)':(r<0).mean()*100,'med':r.median()*100})).unstack()
print('\nAlcista, 365 d, por año de entrada'); print(g.round(0).to_string())
# por ticker
g=x.groupby('t').r.apply(lambda r: pd.Series({'P(<+25%)':(r<0.25).mean()*100,'P(<0)':(r<0).mean()*100})).unstack().sort_values('P(<+25%)')
print('\nAlcista, 365 d, por ticker'); print(g.round(0).T.to_string())
