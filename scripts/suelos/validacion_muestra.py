"""Validacion del pullback RSI2<10 en tendencia + VIX>VIX3M en tickers distintos de los 27 originales.
Uso: python scripts/suelos/validacion_muestra.py <carpeta> <yf_val_ok.pkl> (necesita yf.pkl de la ronda original; escribe G_<fichero>)"""
import pandas as pd, numpy as np, sys, sqlite3
sp=sys.argv[1]; fn=sys.argv[2]
Y=pd.read_pickle(sp+'/'+fn); V=pd.read_pickle(sp+'/yf.pkl')
vix=V['^VIX']['Close'].dropna(); v3=V['^VIX3M']['Close'].dropna(); backw=((vix/v3)>1)
def rsi(s,n):
    x=s.diff(); u=x.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); w=(-x.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); return 100-100/(1+u/w)
def feats(t):
    x=Y[t].dropna(subset=['Close','High','Low','Open']); x=x[x.Volume>0]
    if len(x)<400: return None
    h,l,c=x.High,x.Low,x.Close; pc=c.shift()
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); a10=tr.rolling(10).mean()
    sma200=c.rolling(200).mean(); sma50=c.rolling(50).mean(); trend=(c>sma200)&(sma50>sma200)
    kl=c.ewm(span=20,adjust=False).mean()-2*a10
    H=np.column_stack([h.shift(-i).values for i in range(1,11)]); C10=c.shift(-10).values
    touch=(np.nanmax(np.where(np.isnan(H),-np.inf,H),axis=1)>=(c+1.5*atr).values).astype(float)
    ok=~np.isnan(H).any(1)&~np.isnan(C10)&sma200.notna().values
    F=pd.DataFrame({'pb':((rsi(c,2)<10)&trend).values,'klb':(c<kl).values,'y2':touch,'y1':(C10>c.values).astype(float),'r10':C10/c.values-1,'ok':ok,'close':c.values,'atr':atr.values},index=x.index)
    F['tk']=t; return F[F.ok]
tks=[t for t in Y.columns.get_level_values(0).unique()]
fs=[f for f in (feats(t) for t in tks) if f is not None]
G=pd.concat(fs); G['backw']=G.index.map(backw).fillna(False).astype(bool); G=G.sort_index()
print('tickers usados',G.tk.nunique(),'filas',len(G),G.index.min().date(),G.index.max().date())
G.to_pickle(sp+'/G_'+fn)
rng=np.random.default_rng(1)
def episodes(z,y):
    d=z.groupby(level=0)[y].agg(['mean','size']).sort_index(); ds=d.index.to_series(); d['ep']=(ds.diff().dt.days.fillna(99)>10).cumsum().values
    return d,d.groupby('ep').apply(lambda g:pd.Series({'p':np.average(g['mean'],weights=g['size']),'f':g['mean'].iloc[0],'ini':g.index[0],'n':g['size'].sum()}),include_groups=False)
def rep(nm,m,y):
    z=G[m]
    if len(z)==0: print(nm,'sin casos'); return
    d,E=episodes(z,y); bs=[E.p.values[rng.integers(0,len(E),len(E))].mean() for _ in range(4000)]
    h=E.ini<'2019-01-01'
    print(f'{nm:42s} {y} base={G[y].mean()*100:4.1f} filas={len(z):6d} dias={len(d):4d} ep={len(E):4d} Pfilas={z[y].mean()*100:5.1f} Pdia={d["mean"].mean()*100:5.1f} Pep={E.p.mean()*100:5.1f} [{np.percentile(bs,2.5)*100:4.1f},{np.percentile(bs,97.5)*100:4.1f}] 1erDia={E.f.mean()*100:5.1f} | 04-18 {E.p[h].mean()*100:5.1f}(n={h.sum()}) 19-26 {E.p[~h].mean()*100:5.1f}(n={(~h).sum()})')
for y in ('y2','y1'):
    print()
    rep('pullback + backw',G.pb&G.backw,y)
    rep('  y bajo Keltner',G.pb&G.backw&G.klb,y)
    rep('  y no bajo Keltner',G.pb&G.backw&~G.klb,y)
    rep('pullback solo',G.pb,y); rep('backw solo',G.backw,y)
