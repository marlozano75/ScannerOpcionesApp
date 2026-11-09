"""Validacion fuera de muestra, paso 4: diferencia frente al control del MISMO dia de backwardation (pullback vs todos, bajo Keltner vs no) por dia y por episodio, con bootstrap y por años. Necesita $D/G_yf_val_ok.pkl de validacion_muestra.py.
Uso: python scripts/suelos/validacion_control.py $D"""
import pandas as pd, numpy as np, sys
D=sys.argv[1]; G=pd.read_pickle(D+'/G_yf_val_ok.pkl'); rng=np.random.default_rng(2)
G=G[G.backw]
G['day']=G.index
def run(y,sel,ctl,nm):
    # por dia backw: P(señal) - P(control mismo dia); episodios entre los dias con señal
    a=G[sel].groupby('day')[y].agg(['mean','size']); b=G[ctl].groupby('day')[y].mean()
    d=a.join(b.rename('c'),how='inner'); d['diff']=d['mean']-d['c']; d=d.sort_index()
    ds=d.index.to_series(); d['ep']=(ds.diff().dt.days.fillna(99)>10).cumsum().values
    E=d.groupby('ep').apply(lambda g:pd.Series({'diff':g['diff'].mean(),'ini':g.index[0],'n':g['size'].sum(),'first':g['diff'].iloc[0]}),include_groups=False)
    bs=[E['diff'].values[rng.integers(0,len(E),len(E))].mean() for _ in range(5000)]
    h=E.ini<'2019-01-01'
    print(f'{nm:44s} {y}: dias={len(d)} ep={len(E)} diff vs control mismo dia: por dia {d["diff"].mean()*100:+5.1f} | por episodio {E["diff"].mean()*100:+5.1f} IC95[{np.percentile(bs,2.5)*100:+5.1f},{np.percentile(bs,97.5)*100:+5.1f}] P(<=0)={(np.array(bs)<=0).mean():.3f} | primer dia {E["first"].mean()*100:+5.1f} | 04-18 {E["diff"][h].mean()*100:+5.1f}(n={h.sum()}) 19-26 {E["diff"][~h].mean()*100:+5.1f}(n={(~h).sum()}) | ep con diff>0: {(E["diff"]>0).mean()*100:.0f}%')
allm=G.close>0
for y in ('y2','y1'):
    run(y,G.pb,allm,'pullback vs todos (dias backw)')
    run(y,G.pb&G.klb,G.pb&~G.klb,'pullback bajo Keltner vs pullback no bajo')
    run(y,G.pb&G.klb,allm,'pullback bajo Keltner vs todos')
    run(y,G.klb,allm,'bajo Keltner (sin RSI2) vs todos')
print('\nTickers por dia backw con señal pb: mediana',G[G.pb].groupby('day').size().median(),'| max',G[G.pb].groupby('day').size().max())
yr=G[G.pb].groupby(G[G.pb].index.year)['y2'].agg(['mean','size']); c=G.groupby(G.index.year)['y2'].mean()
print(pd.concat([yr.rename(columns={'mean':'P pb','size':'filas'}),c.rename('P todos')],axis=1).dropna().assign(**{'P pb':lambda x:(x['P pb']*100).round(1),'P todos':lambda x:(x['P todos']*100).round(1)}).to_string())
