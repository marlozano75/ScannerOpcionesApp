"""Medición por episodio del pullback RSI2<10 en tendencia + VIX>VIX3M (2026-11-09).
Uso: python episodios.py <carpeta con yf.pkl y CDS.pkl>"""
import pandas as pd, numpy as np, sys
sp=sys.argv[1]
G=pd.read_pickle(sp+'/CDS.pkl'); Y=pd.read_pickle(sp+'/yf.pkl')
v=Y['^VIX']['Close'].dropna(); v3=Y['^VIX3M']['Close'].dropna()
backw=((v/v3)>1).fillna(False)
G['backw']=G.index.map(backw).fillna(False).astype(bool)
G=G[G.r10p.notna()].sort_index()
G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']
m=G['n_pullback: RSI2<10 en tendencia']&G.backw
z=G[m]
print('base (todos) y1 %.1f y2 %.1f'%(G.y1.mean()*100,G.y2.mean()*100))
rng=np.random.default_rng(0)
for y in ('y2','y1'):
    d=z.groupby(level=0)[y].agg(['mean','size']).sort_index()
    ds=d.index.to_series(); ep=(ds.diff().dt.days.fillna(99)>10).cumsum(); d['ep']=ep.values
    E=d.groupby('ep').apply(lambda g: pd.Series({'ini':g.index[0],'dias':len(g),'filas':g['size'].sum(),'p':np.average(g['mean'],weights=g['size']),'p_dia':g['mean'].mean()}))
    first=z.groupby(level=0)[y].mean().loc[d.groupby('ep').head(1).index]  # primer dia de cada episodio
    print(f'\n=== {y}: filas={len(z)} dias={len(d)} episodios={len(E)}')
    print('P filas %.1f | P dia medio %.1f | P episodio medio (cada episodio pesa 1) %.1f | mediana ep %.1f | primer dia del episodio %.1f'%(z[y].mean()*100,d['mean'].mean()*100,E.p.mean()*100,E.p.median()*100,first.mean()*100))
    print('episodios con P>=50%%: %d de %d (%.0f%%) | con P=0: %d'%((E.p>=.5).sum(),len(E),(E.p>=.5).mean()*100,(E.p==0).sum()))
    bs=[E.p.values[rng.integers(0,len(E),len(E))].mean() for _ in range(5000)]
    print('IC95%% bootstrap por episodios de P(ep): %.1f - %.1f'%(np.percentile(bs,2.5)*100,np.percentile(bs,97.5)*100))
    E['ini']=pd.to_datetime(E.ini)
    for nm,mk in (('2004-18',E.ini<'2019-01-01'),('2019-26',E.ini>='2019-01-01')):
        print(f'  {nm}: episodios={mk.sum()} P ep medio={E.p[mk].mean()*100:.1f}')
    print('  por año (episodios, P ep medio):',{k:(len(g),round(g.p.mean()*100)) for k,g in E.groupby(E.ini.dt.year)})
    if y=='y2':
        big=E.sort_values('filas',ascending=False).head(6); print('  episodios más grandes:'); print(big.assign(p=(big.p*100).round(1)).to_string())
# sensibilidad al umbral de separación
ds=pd.Series(z.index.unique()).sort_values()
for gap in (3,5,10,20): print('separacion >%d dias -> %d episodios'%(gap,(ds.diff().dt.days.fillna(99)>gap).sum()))
