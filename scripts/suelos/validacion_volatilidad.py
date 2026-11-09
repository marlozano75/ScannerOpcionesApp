"""Validacion, componente de estres de volatilidad propia (2026-11-09): percentil del ATR% en 1 año y cociente frente a su media, dentro del pullback RSI2<10 + VIX>VIX3M y frente al control del mismo dia. Necesita $D/G_yf_val_ok.pkl de validacion_muestra.py.
Uso: python scripts/suelos/validacion_volatilidad.py $D"""
import pandas as pd, numpy as np, sys
D=sys.argv[1]; G=pd.read_pickle(D+'/G_yf_val_ok.pkl'); rng=np.random.default_rng(3)
G['ap']=G.atr/G.close; G['tk']=G.tk.astype('category'); G['day']=G.index
g=G.groupby('tk',observed=True)['ap']
G['ratio']=G.ap/g.transform(lambda s:s.rolling(252,min_periods=200).mean())            # ATR% actual frente a su media de 1 año
G['rank']=g.transform(lambda s:s.rolling(252,min_periods=200).rank(pct=True))           # percentil del ATR% en el ultimo año (tipo HV Rank)
G=G[G.backw&G['rank'].notna()]
print('filas backw con proxy',len(G),'| pb',int(G.pb.sum()))
def run(y,sel,ctl,nm):
    a=G[sel].groupby('day')[y].agg(['mean','size']); b=G[ctl].groupby('day')[y].mean()
    d=a.join(b.rename('c'),how='inner').sort_index(); d['diff']=d['mean']-d['c']
    ds=d.index.to_series(); d['ep']=(ds.diff().dt.days.fillna(99)>10).cumsum().values
    E=d.groupby('ep').apply(lambda g:pd.Series({'diff':g['diff'].mean(),'p':g['mean'].mean(),'ini':g.index[0],'first':g['diff'].iloc[0],'n':g['size'].sum()}),include_groups=False)
    bs=[E['diff'].values[rng.integers(0,len(E),len(E))].mean() for _ in range(4000)]; h=E.ini<'2019-01-01'
    print(f'{nm:50s} {y}: filas={int(d["size"].sum()):6d} ep={len(E):3d} P={d["mean"].mean()*100:5.1f} | dif vs control {E["diff"].mean()*100:+5.1f} IC95[{np.percentile(bs,2.5)*100:+5.1f},{np.percentile(bs,97.5)*100:+5.1f}] 1erDia {E["first"].mean()*100:+5.1f} | 04-18 {E["diff"][h].mean()*100:+5.1f}(n={h.sum()}) 19-26 {E["diff"][~h].mean()*100:+5.1f}(n={(~h).sum()})')
allm=G.close>0
for y in ('y2','y1'):
    print()
    for nm,col,th in (('rank (percentil ATR% 1 año)','rank',(.5,.8)),('ratio (ATR%/media 1 año)','ratio',(1.0,1.5))):
        run(y,G.pb&(G[col]>=th[1]),G.pb&(G[col]<th[0]),f'pb: {nm} alto (>={th[1]}) vs bajo (<{th[0]})')
        run(y,G.pb&(G[col]>=th[1]),allm,f'pb: {nm} alto vs todos mismo dia')
        run(y,G.pb&(G[col]<th[0]),allm,f'pb: {nm} bajo vs todos mismo dia')
    run(y,G.pb&G.klb&(G['rank']>=.8),G.pb&G.klb&(G['rank']<.5),'pb+klb: rank alto vs bajo')
    run(y,(G['rank']>=.8),allm,'SIN RSI2: rank>=0.8 vs todos mismo dia')
print('\nreparto del pullback+backw por rank:',pd.cut(G.loc[G.pb,'rank'],[0,.2,.4,.6,.8,1.0]).value_counts(normalize=True).sort_index().round(2).to_dict())
