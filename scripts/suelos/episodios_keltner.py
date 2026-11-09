"""Pullback RSI2<10 + VIX>VIX3M separado por Keltner del ticker (2026-11-09): P por fila, dia, episodio y primer dia.
Uso: python episodios_keltner.py <carpeta con yf.pkl y CDS.pkl>"""
import pandas as pd, numpy as np, sys
sp=sys.argv[1]
G=pd.read_pickle(sp+'/CDS.pkl'); Y=pd.read_pickle(sp+'/yf.pkl')
v=Y['^VIX']['Close'].dropna(); v3=Y['^VIX3M']['Close'].dropna()
G['backw']=G.index.map(((v/v3)>1).fillna(False)).fillna(False).astype(bool)
G=G.sort_index(); G['day']=G.index; G.index.name=None
G['kl3']=G.groupby('tk')['kl_below'].transform(lambda s:s.rolling(3,min_periods=1).max()).astype(bool)
# distancia a la banda en ATR (negativa = bajo la banda); aproximada con kl_below solo si no hay mas: se calcula desde yf
def dist(t):
    x=Y[t].dropna(subset=['Close','High','Low']); h,l,c=x.High,x.Low,x.Close; pc=c.shift()
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); a10=tr.rolling(10).mean()
    kl=c.ewm(span=20,adjust=False).mean()-2*a10; return ((c-kl)/a10).rename('kdist')
K=pd.concat([dist(t).to_frame().assign(tk=t) for t in G.tk.unique()]); K.index.name='day'
G=G.merge(K.reset_index(),on=['day','tk'],how='left').set_index('day').sort_index()
G=G[G.r10p.notna()]; G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']
pb=G['n_pullback: RSI2<10 en tendencia']
def rep(nm,m,y='y2'):
    z=G[m]
    if len(z)==0: print(f'{nm:52s} sin casos'); return
    d=z.groupby(level=0)[y].agg(['mean','size']).sort_index(); ds=d.index.to_series(); ep=(ds.diff().dt.days.fillna(99)>10).cumsum().values; d['ep']=ep
    E=d.groupby('ep').apply(lambda g:pd.Series({'p':np.average(g['mean'],weights=g['size']),'f':g['mean'].iloc[0],'ini':g.index[0]}))
    h1=E.ini<'2019-01-01'
    print(f'{nm:52s} filas={len(z):4d} dias={len(d):4d} ep={len(E):3d} Pfilas={z[y].mean()*100:5.1f} Pdia={d["mean"].mean()*100:5.1f} Pep={E.p.mean()*100:5.1f} 1erDia={E.f.mean()*100:5.1f} | ep 04-18 {E.p[h1].mean()*100:5.1f} (n={h1.sum()}) 19-26 {E.p[~h1].mean()*100:5.1f} (n={(~h1).sum()})')
for y in ('y2','y1'):
    print('\n##',y,'base %.1f'%(G[y].mean()*100)); b=pb&G.backw
    rep('pullback + backw',b,y)
    rep('  ... y ticker bajo Keltner (hoy)',b&G.kl_below,y)
    rep('  ... y ticker NO bajo Keltner',b&~G.kl_below,y)
    rep('  ... y bajo Keltner en los ultimos 3d',b&G.kl3,y)
    rep('  ... y NO bajo Keltner en 3d',b&~G.kl3,y)
    q=G.loc[b,'kdist'].quantile([.33,.67]).values
    rep('  ... kdist tercil bajo (mas cerca/bajo banda)',b&(G.kdist<=q[0]),y); rep('  ... kdist tercil medio',b&(G.kdist>q[0])&(G.kdist<=q[1]),y); rep('  ... kdist tercil alto',b&(G.kdist>q[1]),y)
    rep('pullback sin backw',pb&~G.backw,y)
    rep('pullback sin backw y bajo Keltner',pb&~G.backw&G.kl_below,y)
    rep('backw + bajo Keltner (sin RSI2)',G.backw&G.kl_below,y)
    rep('bajo Keltner solo',G.kl_below,y)
    rep('backw solo',G.backw,y)
    rep('pullback solo',pb,y)
print('\nkdist en pullback+backw: ',G.loc[pb&G.backw,'kdist'].describe().round(2).to_dict())
