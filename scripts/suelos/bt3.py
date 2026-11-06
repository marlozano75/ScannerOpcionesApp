import numpy as np, pandas as pd, sys
sp=sys.argv[1]; d=pd.read_pickle(sp+'/yf.pkl')
T='SPY QQQ IWM AAPL MSFT NVDA AMZN META GOOGL TSLA AMD NFLX JPM BAC XOM INTC MU PLTR COIN SOFI UBER DIS BA XLF GLD TLT SMH'.split()
def rsi(s,n):
    x=s.diff(); u=x.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); w=(-x.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); return 100-100/(1+u/w)
def linreg(s,n):
    x=np.arange(n); xm=x.mean(); den=((x-xm)**2).sum()
    return s.rolling(n).apply(lambda a: a.mean()+((x-xm)*(a-a.mean())).sum()/den*(n-1-xm),raw=True)
def feats(t):
    x=d[t].dropna(subset=['Close','High','Low','Open']).copy(); x=x[x.Volume>0]
    o,h,l,cl,v=x.Open,x.High,x.Low,x.Close,x.Volume; pc=cl.shift()
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); atr10=tr.rolling(10).mean()
    F=pd.DataFrame(index=x.index)
    ema=cl.ewm(span=20,adjust=False).mean(); kl=ema-2*atr10
    F['kl_in']=((l<kl)&(cl>kl))|((cl.shift()<kl.shift())&(cl>kl))
    sma=cl.rolling(20).mean(); sd=cl.rolling(20).std(); F['pb0']=(cl-(sma-2*sd))<0
    ph,pl=h.shift(),l.shift(); up2=(h>ph)&(l>=pl); dn2=(l<pl)&(h<=ph)
    F['d2u']=dn2.shift()&up2; F['d2u_pb']=F.d2u&F.pb0.shift().fillna(False).astype(bool)
    rng=(h-l).replace(0,np.nan); ibs=(cl-l)/rng; F['rev']=(cl>o)&(ibs>.5)
    F['rv']=v/v.rolling(20).mean().shift(); F['rv3']=F.rv.rolling(3).max()
    # caidas en ATR
    for N in (5,10,20):
        mx=h.rolling(N).max()
        for k in (2,3,4,5):
            F[f'dA{N}_{k}']=((mx-cl)/atr)>=k          # cierre vs maximo de N dias en ATR
            F[f'dL{N}_{k}']=((mx-l)/atr)>=k+1 if False else ((mx-l)/atr)>=k   # minimo vs maximo
    # Mogalef variantes
    wp=(o+h+l+2*cl)/5
    for lr in (3,5,10):
        med=linreg(wp,lr)
        for sdn in (7,14,20):
            s=med.rolling(sdn).std()
            for k in (1.5,2,2.5):
                lo=med-k*s; b=f'mog{lr}_{sdn}_{k}'
                F[b+'_t']=l<lo; F[b+'_b']=(l<lo)&(cl>lo); F[b+'_r']=(cl.shift()<lo.shift())&(cl>lo)
    # Mogalef canal sobre cierre (regresion 20/ residuo)
    for n in (20,50):
        lrc=linreg(cl,n); res=(cl-lrc).rolling(n).std()
        for k in (1.5,2,2.5):
            lo=lrc-k*res; b=f'moc{n}_{k}'
            F[b+'_t']=l<lo; F[b+'_b']=(l<lo)&(cl>lo); F[b+'_r']=(cl.shift()<lo.shift())&(cl>lo)
    # MACD
    for (f,s_,g) in ((12,26,9),(8,17,9),(5,35,5),(19,39,9)):
        m=cl.ewm(span=f,adjust=False).mean()-cl.ewm(span=s_,adjust=False).mean(); sg=m.ewm(span=g,adjust=False).mean(); hs=m-sg; b=f'macd{f}_{s_}_{g}'
        F[b+'_x0']=(m>sg)&(m.shift()<=sg.shift())&(m<0)                       # cruce al alza bajo cero
        F[b+'_turn']=(hs<0)&(hs>hs.shift())&(hs.shift()<hs.shift(2))             # histograma gira al alza estando negativo
        F[b+'_turn2']=(hs<0)&(hs>hs.shift())&(hs.shift()>hs.shift(2)) if False else (hs<0)&(hs>hs.shift())&(hs.shift()>hs.shift(2))&(hs.shift(2)>hs.shift(3))
        q=(m/cl).rolling(250).quantile(0.05); F[b+'_ext']=(m/cl)<q               # MACD en su extremo bajo (5%) del ultimo año
        F[b+'_extturn']=F[b+'_ext'].rolling(3).max().astype(bool)&(hs>hs.shift())
        # divergencia alcista: nuevo minimo de 20d en precio con MACD por encima de su minimo previo
        newlow=l<=l.shift().rolling(20).min(); prevmin=m.shift().rolling(20).min()
        F[b+'_div']=newlow&(m>prevmin)&(m<0)
        F[b+'_div_rev']=F[b+'_div'].shift().fillna(False).astype(bool)&(cl>pc)
    # etiquetas
    W=7; swing=(l<=l.rolling(2*W+1,center=True).min())
    fmax=h[::-1].rolling(10,min_periods=10).max()[::-1].shift(-1); swing=swing&((fmax-l)>=2*atr)
    F['lab']=(swing|swing.shift(1)).astype(float); F['lab0']=swing.astype(float)
    F.loc[F.index>d[t].dropna(subset=['Close']).index[-16],['lab','lab0']]=np.nan     # sin confirmacion posible
    F['r5']=cl.shift(-5)/cl-1; F['r10']=cl.shift(-10)/cl-1
    fl=l[::-1].rolling(10,min_periods=10).min()[::-1].shift(-1); F['nl10']=(fl>=l).astype(float); F.loc[fl.isna(),'nl10']=np.nan; F['mae']=fl/cl-1
    F['close']=cl; F['atr']=atr; F['tk']=t; F['kl_below']=cl<kl
    F['ok']=cl.rolling(200).count().notna()
    return F
A=pd.concat([feats(t) for t in T]); A.index.name='day'; A=A[A.ok]
br=A.groupby(level=0)['kl_below'].agg(['mean','count']); A['breadth']=A.index.map(br['mean'].where(br['count']>=8))
A.to_pickle(sp+'/B.pkl'); print(A.shape,A.index.max())
