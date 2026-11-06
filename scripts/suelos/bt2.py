import numpy as np, pandas as pd, sys
sp=sys.argv[1]; d=pd.read_pickle(sp+'/yf.pkl')
T='SPY QQQ IWM AAPL MSFT NVDA AMZN META GOOGL TSLA AMD NFLX JPM BAC XOM INTC MU PLTR COIN SOFI UBER DIS BA XLF GLD TLT SMH'.split()
def rsi(s,n):
    x=s.diff(); u=x.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); w=(-x.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); return 100-100/(1+u/w)
def feats(t):
    x=d[t].dropna(subset=['Close','High','Low','Open']).copy(); x=x[x.Volume>0]
    o,h,l,cl,v=x.Open,x.High,x.Low,x.Close,x.Volume; pc=cl.shift()
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); atr10=tr.rolling(10).mean()
    ema=cl.ewm(span=20,adjust=False).mean(); kl=ema-2*atr10
    sma20=cl.rolling(20).mean(); sd=cl.rolling(20).std(); pb=(cl-(sma20-2*sd))/(4*sd)
    ph,pl=h.shift(),l.shift(); up2=(h>ph)&(l>=pl); dn2=(l<pl)&(h<=ph)
    rng=(h-l).replace(0,np.nan); ibs=(cl-l)/rng; rv=v/v.rolling(20).mean().shift()
    F=pd.DataFrame(index=x.index)
    F['kl_in']=(l<kl)&(cl>kl); F['kl_low']=l<kl; F['kl_rev']=(cl.shift()<kl.shift())&(cl>kl)
    F['pb0']=pb<0; F['rsi5']=rsi(cl,5)<20; F['rsi14']=rsi(cl,14)<30; F['rsi2']=rsi(cl,2)<10
    F['d2u']=dn2.shift()&up2; F['f2d']=(l<pl)&(cl>pl)
    F['drop3']=(h.rolling(10).max()-cl)>=3*atr
    F['rev']=(cl>o)&(ibs>0.5); F['up2']=up2
    F['rv']=rv; F['rv_max3']=rv.rolling(3).max(); F['rv_max5']=rv.rolling(5).max()
    F['ibs']=ibs; F['ret1']=cl/pc-1
    F['rvyest']=rv.shift()
    # etiquetas
    W=7; swing=(l<=l.rolling(2*W+1,center=True).min())
    fmax=h[::-1].rolling(10,min_periods=10).max()[::-1].shift(-1); swing=swing&((fmax-l)>=2*atr)
    F['lab']=swing|swing.shift(1); F['lab0']=swing
    F['r5']=cl.shift(-5)/cl-1; F['r10']=cl.shift(-10)/cl-1
    fl=l[::-1].rolling(10,min_periods=10).min()[::-1].shift(-1)
    F['nl10']=fl>=l; F['mae']=fl/cl-1
    F['atrp']=atr/cl; F['tk']=t
    F['kl_below']=(cl<kl)  # para amplitud
    F['valid']=cl.rolling(200).count().notna()&F.r10.notna()
    return F
Fs=[feats(t) for t in T]
A=pd.concat(Fs); A.index.name='day'
# amplitud: fraccion de tickers con cierre bajo Keltner ese dia
br=A.groupby(level=0)['kl_below'].agg(['mean','count']); br=br['mean'].where(br['count']>=8)
A['breadth']=A.index.map(br)
# mercado
def m(t): return d[t]['Close'].dropna()
vix=m('^VIX'); v3=m('^VIX3M'); vv=m('^VVIX'); v9=m('^VIX9D'); sk=m('^SKEW')
M=pd.DataFrame({'vix':vix}); M['v3']=v3; M['vvix']=vv; M['v9']=v9
M['vix_spike']=vix>(vix.rolling(20).mean()+2*vix.rolling(20).std())
M['vix_spike_recent']=M.vix_spike.rolling(3).max().astype(bool)
M['vix_down']=vix<vix.shift()
M['vix_peak_fall']=(vix.rolling(5).max()/vix>1.1)&M.vix_down      # VIX cae >10% desde su maximo de 5d
M['backw']=(M.vix/M.v3)>1
M['backw_recent']=M.backw.rolling(3).max().astype(bool)
M['v9_over_vix']=(M.v9/M.vix)>1
M['vvix_hi']=vv>vv.rolling(252).quantile(0.8)
spy=d['SPY']; sc=spy['Close']; sl=spy['Low']; s_atr10=(spy['High']-spy['Low']).rolling(10).mean()
skl=sc.ewm(span=20,adjust=False).mean()-2*s_atr10
M['spy_osold']=(sl<skl)|(sc<skl); M['spy_osold_recent']=M.spy_osold.rolling(3).max().astype(bool)
M['spy_dd5']=(1-sc/sc.rolling(60).max())>0.05
M['vix_pb']=(vix-(vix.rolling(20).mean()+2*vix.rolling(20).std()))>0
for c in M.columns: A[c]=A.index.map(M[c]) if c not in('vix','v3','vvix','v9') else A.index.map(M[c])
for c in ['vix_spike','vix_spike_recent','vix_down','vix_peak_fall','backw','backw_recent','v9_over_vix','vvix_hi','spy_osold','spy_osold_recent','spy_dd5','vix_pb']:
    A[c]=A[c].fillna(False).astype(bool)
A=A[A.valid]
A.to_pickle(sp+'/A.pkl'); print(len(A),A.index.min(),A.index.max(),A.tk.nunique())
