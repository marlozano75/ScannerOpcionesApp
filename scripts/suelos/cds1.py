import numpy as np, pandas as pd, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); B=pd.read_pickle(sp+'/B2.pkl')
T=sorted(B.tk.unique()); HZ=10
def rsi(s,n):
    x=s.diff(); u=x.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); w=(-x.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); return 100-100/(1+u/w)
spy=Y['SPY']['Close']; spy_up=spy>spy.rolling(200).mean()
parts=[]
for t in T:
    g=B[B.tk==t].copy(); x=Y[t].reindex(g.index); o,h,l,c,v=x.Open,x.High,x.Low,x.Close,x.Volume
    atr=g.atr
    # --- resultados (carrera arriba/abajo) y extremos
    H=np.column_stack([h.shift(-i).values for i in range(1,HZ+1)]); Lo=np.column_stack([l.shift(-i).values for i in range(1,HZ+1)])
    C=np.column_stack([c.shift(-i).values for i in range(1,HZ+1)])
    for k in (1.0,1.5,2.0):
        up=(c.values+k*atr.values)[:,None]; dn=(c.values-k*atr.values)[:,None]
        hu=H>=up; hd=Lo<=dn
        fu=np.where(hu.any(1),hu.argmax(1),99); fd=np.where(hd.any(1),hd.argmax(1),99)
        g[f'win{k}']=(fu<fd).astype(float); g[f'lose{k}']=((fd<=fu)&(fd<99)).astype(float)
        g[f'touch{k}']=hu.any(1).astype(float)
    ok=~np.isnan(H).any(1)&~np.isnan(Lo).any(1)
    g['mfe']=(np.nanmax(H,1)-c.values)/atr.values; g['mae_a']=(np.nanmin(Lo,1)-c.values)/atr.values
    g['r5a']=(C[:,4]-c.values)/atr.values; g['r10a']=(C[:,9]-c.values)/atr.values
    g['r10p']=C[:,9]/c.values-1
    g.loc[~ok,['win1.0','win1.5','win2.0','lose1.0','lose1.5','lose2.0','touch1.0','touch1.5','touch2.0','mfe','mae_a','r5a','r10a','r10p']]=np.nan
    # --- señales nuevas
    sma200=c.rolling(200).mean(); sma50=c.rolling(50).mean(); ema20=c.ewm(span=20,adjust=False).mean(); ema9=c.ewm(span=9,adjust=False).mean()
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); atr10=tr.rolling(10).mean()
    trend=(c>sma200)&(sma50>sma200)
    m=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean(); sg=m.ewm(span=9,adjust=False).mean()
    rv=v/v.rolling(20).mean().shift(); r2=rsi(c,2); r14=rsi(c,14)
    S={}
    S['tendencia (c>SMA200 & SMA50>SMA200)']=trend
    S['pullback: RSI2<10 en tendencia']=(r2<10)&trend
    S['pullback: RSI2<10 y c>SMA200']=(r2<10)&(c>sma200)
    S['pullback a EMA20 en tendencia + vela alcista']=trend&(l.rolling(3).min()<=ema20)&(c>ema20)&(c>o)
    S['ruptura max 20d (cierre)']=c>=c.shift().rolling(20).max()
    S['ruptura max 55d (cierre)']=c>=c.shift().rolling(55).max()
    S['ruptura max 20d + vol>=1.5']=(c>=c.shift().rolling(20).max())&(rv>=1.5)
    S['cruce EMA20 al alza']=(c>ema20)&(c.shift()<=ema20.shift())
    S['cruce EMA9>EMA20 en tendencia']=trend&(ema9>ema20)&(ema9.shift()<=ema20.shift())
    S['MACD cruce al alza (<0)']=(m>sg)&(m.shift()<=sg.shift())&(m<0)
    S['MACD cruce al alza (>0)']=(m>sg)&(m.shift()<=sg.shift())&(m>0)
    S['Keltner superior (c>EMA20+2ATR10)']=c>ema20+2*atr10
    S['2U + vol>=1.5 en tendencia']=trend&(h>h.shift())&(l>=l.shift())&(rv>=1.5)&(c>o)
    S['gap alcista>1ATR y cierra sobre apertura']=((o-c.shift())>atr)&(c>o)
    S['RSI14 cruza 50 al alza']=(r14>50)&(r14.shift()<=50)
    S['vela alcista fuerte (cierre-apertura>=1.5ATR)']=(c-o)>=1.5*atr
    for k_,s_ in S.items(): g['n_'+k_]=s_.fillna(False).values
    g['spy_up']=spy_up.reindex(g.index).fillna(False).values
    g['trend']=trend.fillna(False).values
    g['bounce1']=((c-l)>=atr).values
    g['dl']=l.values; g['dopen']=o.values
    parts.append(g)
G=pd.concat(parts); G.to_pickle(sp+'/CDS.pkl'); print(G.shape)
