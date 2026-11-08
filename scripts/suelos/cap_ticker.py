"""Ronda R11: ¿qué señal por ticker suma sobre los dias de capitulacion (VIX>VIX3M y >=20 % de la cesta bajo Keltner)?
Uso: python cap_ticker.py <carpeta con CDS.pkl, A.pkl, FV.pkl>"""
import sys, pandas as pd, numpy as np
sp=sys.argv[1]; G=pd.read_pickle(sp+'/CDS.pkl'); A=pd.read_pickle(sp+'/A.pkl'); F=pd.read_pickle(sp+'/FV.pkl')
M=A[['backw']].groupby(level=0).first(); G=G.join(M,how='left'); G['backw']=G.backw.fillna(False).astype(bool)
G=G.reset_index().merge(F.reset_index(),on=['day','tk'],how='left',suffixes=('','_fv')); G=G[G.r10p.notna()]
G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']; G['w']=G['win1.5']
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']; G['score']=G[fam].sum(axis=1)
for s in (2,3,4): G[f'score>={s}']=G.score>=s
cap=G[G.backw&(G.breadth>=.2)]; rest=G[~(G.backw&(G.breadth>=.2))]
cols=[c for c in G.columns if G[c].dtype==bool and c not in('lose','ok','backw','spy_up')]
def st(z): return len(z),z.day.nunique(),z.y1.mean()*100,z.y2.mean()*100,z.w.mean()*100,z['lose1.5'].mean()*100
n,d,a,b,c,l=st(cap); print(f'CAPITULACION sola: n={n} dias={d} cierra>ent={a:.1f}% toca={b:.1f}% gana={c:.1f}% pierde={l:.1f}%')
print(f'  IS {cap[cap.day<"2019-01-01"].y1.mean()*100:.1f}% / OOS {cap[cap.day>="2019-01-01"].y1.mean()*100:.1f}%\n')
rows=[]
for k in cols:
    z=cap[cap[k]]
    if len(z)<250: continue
    n,d,a,b,c,l=st(z); i=z[z.day<'2019-01-01']; o=z[z.day>='2019-01-01']
    rows.append((k,n,d,a,b,c,l,i.y1.mean()*100 if len(i)>30 else np.nan,o.y1.mean()*100 if len(o)>30 else np.nan, rest[rest[k]].y1.mean()*100))
R=pd.DataFrame(rows,columns=['senal','n','dias','cierra','toca','gana','pierde','IS','OOS','fuera_cap']).sort_values('cierra',ascending=False)
pd.set_option('display.width',250); pd.set_option('display.max_colwidth',60)
print('Mejores (n>=250 filas dentro de capitulacion):'); print(R.head(18).round(1).to_string(index=False))
print('\nPeores:'); print(R.tail(5).round(1).to_string(index=False))
