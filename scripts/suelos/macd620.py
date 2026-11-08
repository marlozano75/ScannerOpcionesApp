"""Ronda R10: MACD 6-20-9 cruzando al alza la señal con el precio sobre la EMA10 (setup '620 MACD cross') y variantes.
Uso: python macd620.py <carpeta con yf.pkl, CDS.pkl, A.pkl>"""
import pandas as pd, numpy as np, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); G=pd.read_pickle(sp+'/CDS.pkl'); A=pd.read_pickle(sp+'/A.pkl')
ema=lambda s,n:s.ewm(span=n,adjust=False).mean()
rows=[]
for t in sorted(G.tk.unique()):
    x=Y[t].dropna(subset=['Close']); x=x[x.Volume>0]; c=x.Close
    m=ema(c,6)-ema(c,20); s=ema(m,9); e10=ema(c,10); sma50=c.rolling(50).mean(); sma200=c.rolling(200).mean()
    cross=(m>s)&(m.shift()<=s.shift())
    D=pd.DataFrame({'cross':cross,'e10':c>e10,'neg':m<0,'tr':(c>sma200)&(sma50>sma200),'dn':c<sma50,
        'vol':x.Volume>1.5*x.Volume.rolling(20).mean(),'lo20':(c.rolling(20).min().shift(0)<=c.rolling(20).min())&(c.rolling(10).min()<c.rolling(60).min().shift(10)*1.02)})
    D['tk']=t; D.index.name='day'; rows.append(D.reset_index())
F=pd.concat(rows)
mk=['backw','vvix_hi','spy_osold']; M=A[mk].groupby(level=0).first()
G=G.join(M,how='left'); G[mk]=G[mk].fillna(False).astype(bool); G=G[G.r10p.notna()].reset_index()
G=G.merge(F,on=['day','tk'],how='left'); 
for k in ['cross','e10','neg','tr','dn','vol','lo20']: G[k]=G[k].fillna(False).astype(bool)
G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']; G['w']=G['win1.5']; G['l']=G['lose1.5']
def row(nm,z):
    if len(z)<25: print(f'{nm:60s} n={len(z)} (pocos)'); return
    d=z.day.nunique(); ds=pd.Series(sorted(z.day.unique())); ep=int((ds.diff().dt.days.fillna(99)>10).sum())
    a=z[z.day<'2019-01-01']; o=z[z.day>='2019-01-01']
    print(f'{nm:60s} n={len(z):6d} dias={d:4d} ep={ep:3d} P(cierre>ent)={z.y1.mean()*100:5.1f}% (dia {z.groupby("day").y1.mean().mean()*100:4.1f}, IS {a.y1.mean()*100:4.1f}/OOS {o.y1.mean()*100:4.1f}) toca={z.y2.mean()*100:5.1f}% gana={z.w.mean()*100:4.1f}% pierde={z.l.mean()*100:4.1f}% r10={z.r10p.mean()*100:5.2f}%')
row('BASE',G)
row('cruce MACD(6,20,9) al alza',G[G.cross])
row('cruce + cierre > EMA10 (setup 620)',G[G.cross&G.e10])
row('cruce + EMA10 + MACD<0 (cruce bajo cero)',G[G.cross&G.e10&G.neg])
row('cruce + EMA10 + MACD>0',G[G.cross&G.e10&~G.neg])
row('cruce + EMA10 + tendencia (c>SMA200, SMA50>SMA200)',G[G.cross&G.e10&G.tr])
row('cruce + EMA10 + bajo SMA50 (pullback)',G[G.cross&G.e10&G.dn])
row('cruce + EMA10 + tendencia + MACD<0 (pullback en tendencia)',G[G.cross&G.e10&G.tr&G.neg])
row('cruce + EMA10 + volumen>=1,5x',G[G.cross&G.e10&G.vol])
row('cruce + EMA10 + VIX>VIX3M',G[G.cross&G.e10&G.backw])
row('cruce + EMA10 + VVIX alto',G[G.cross&G.e10&G.vvix_hi])
row('cruce + EMA10 + amplitud>=20%',G[G.cross&G.e10&(G.breadth>=.2)])
row('cruce + EMA10 + score>=3',G[G.cross&G.e10&(G[['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']].sum(axis=1)>=3)])
pb='n_pullback: RSI2<10 en tendencia'
row('cruce + EMA10 + pullback RSI2<10 en tendencia',G[G.cross&G.e10&G[pb]])
row('cruce + EMA10 con RSI2<10 en las 5 sesiones previas (tendencia)',G[G.cross&G.e10&(G.groupby('tk')[pb].transform(lambda s:s.rolling(5,min_periods=1).max())>0)])
row('(ref.) pullback RSI2<10 en tendencia + VIX>VIX3M',G[G[pb]&G.backw])
row('(ref.) pullback + backw + cruce 620 (mismo dia)',G[G[pb]&G.backw&G.cross])
row('(ref.) pullback + backw + cruce 620 en las 5 sesiones',G[G[pb]&G.backw&(G.groupby('tk').cross.transform(lambda s:s.rolling(5,min_periods=1).max())>0)])
