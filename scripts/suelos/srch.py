import pandas as pd, numpy as np, sys, itertools
sp=sys.argv[1]; G=pd.read_pickle(sp+'/CDS.pkl'); A=pd.read_pickle(sp+'/A.pkl'); F=pd.read_pickle(sp+'/FV.pkl')
mk=['vix_spike','vix_spike_recent','vix_peak_fall','backw','backw_recent','v9_over_vix','vvix_hi','spy_osold','spy_osold_recent','spy_dd5','vix_pb']
M=A[mk].groupby(level=0).first()
G=G.join(M,how='left'); G[mk]=G[mk].fillna(False).astype(bool)
G=G.reset_index().merge(F.reset_index(),on=['day','tk'],how='left',suffixes=('','_fv')).set_index('day').sort_index()
G=G[G.r10p.notna()]
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']; G['score']=G[fam].sum(axis=1)
for s in (2,3,4): G[f'score>={s}']=G.score>=s
for th in (.1,.2,.35,.5): G[f'amplitud>={int(th*100)}']=G.breadth>=th
G['rebote>=1ATR']=G.bounce1
bool_cols=[c for c in G.columns if G[c].dtype==bool and c not in('lose','ok')]
X=G[bool_cols].astype(np.float32).values; names=bool_cols
keep=[i for i in range(X.shape[1]) if X[:,i].sum()>=400]; X=X[:,keep]; names=[names[i] for i in keep]
print('features',len(names),'filas',len(G))
IS=(G.index<'2019-01-01'); 
Ys={'cierra por encima a 10d (r10>0)':(G.r10p>0).values.astype(np.float32),'toca +1.5 ATR en 10d':G['touch1.5'].values.astype(np.float32)}
res=[]
for yn,y in Ys.items():
    print('\n#####',yn,' base=%.1f%% (IS %.1f/OOS %.1f)'%(y.mean()*100,y[IS].mean()*100,y[~IS].mean()*100))
    Xi,yi=X[IS],y[IS]; Xo,yo=X[~IS],y[~IS]
    n_i=Xi.T@Xi; s_i=(Xi*yi[:,None]).T@Xi; n_o=Xo.T@Xo; s_o=(Xo*yo[:,None]).T@Xo
    pi=np.where(n_i>0,s_i/np.maximum(n_i,1),0); po=np.where(n_o>0,s_o/np.maximum(n_o,1),0)
    rows=[]
    for i in range(len(names)):
        for j in range(i,len(names)):
            if n_i[i,j]>=300 and n_o[i,j]>=150 and pi[i,j]>=0.62:
                rows.append((names[i] if i==j else names[i]+' & '+names[j],int(n_i[i,j]),pi[i,j]*100,int(n_o[i,j]),po[i,j]*100))
    R=pd.DataFrame(rows,columns=['senal','nIS','pIS','nOOS','pOOS']); R['pmin']=R[['pIS','pOOS']].min(axis=1)
    print('pares/individuales con P_IS>=62%%, n_IS>=300, n_OOS>=150: %d de %d probados'%(len(R),len(names)*(len(names)+1)//2))
    pd.set_option('display.width',220); pd.set_option('display.max_colwidth',90)
    print(R.sort_values('pmin',ascending=False).head(14).round(1).drop(columns='pmin').to_string(index=False))
    print('con P_IS>=65%% y P_OOS>=65%%: %d'%(((R.pIS>=65)&(R.pOOS>=65)).sum()))

# ---------- detalle de los candidatos ----------
G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']
def detail(nm,m,y):
    z=G[m]; d=z.index.unique()
    ds=pd.Series(d).sort_values(); ep=(ds.diff().dt.days.fillna(99)>10).sum()
    byd=z.groupby(level=0)[y].mean()           # un voto por dia
    yr=z.groupby(z.index.year)[y].agg(['mean','size']); good=(yr['mean']>=.60).sum()
    print(f'{nm:62s} filas={len(z):5d} dias={len(d):4d} episodios(>10d)={ep:3d} P(filas)={z[y].mean()*100:4.1f}% P(dia-medio)={byd.mean()*100:4.1f}% anos>=60%: {good}/{len(yr)} (con >=1 señal) | r10 medio={z.r10p.mean()*100:5.2f}% peor10%={z.r10p.quantile(.1)*100:6.1f}%')
print('\n=== DETALLE (Y1 = cierra por encima a los 10 dias)')
y='y1'
detail('base todos los dias',G.score>=0,y)
detail('backw (VIX>VIX3M) solo',G.backw,y); detail('vvix_hi solo',G.vvix_hi,y); detail('macd12_26_9_ext solo',G.macd12_26_9_ext,y)
detail('macd12_26_9_ext & backw',G.macd12_26_9_ext&G.backw,y)
detail('macd12_26_9_ext & vvix_hi',G.macd12_26_9_ext&G.vvix_hi,y)
detail('macd12_26_9_ext & backw & vvix_hi',G.macd12_26_9_ext&G.backw&G.vvix_hi,y)
detail('macd12_26_9_div & vvix_hi',G.macd12_26_9_div&G.vvix_hi,y)
detail('macd5_35_5_ext & fvg_near_bull',G.macd5_35_5_ext&G.fvg_near_bull,y)
print('\n=== DETALLE (Y2 = toca +1.5 ATR en 10 dias)'); y='y2'
detail('base todos los dias',G.score>=0,y)
detail('backw solo',G.backw,y)
detail('pullback RSI2<10 en tendencia & backw',G['n_pullback: RSI2<10 en tendencia']&G.backw,y)
detail('macd12_26_9_ext & amplitud>=50',G.macd12_26_9_ext&G['amplitud>=50'],y)
detail('macd5_35_5_ext & amplitud>=35',G.macd5_35_5_ext&G['amplitud>=35'],y)
detail('vix_spike & score>=4',G.vix_spike&G['score>=4'],y)
detail('backw & amplitud>=20',G.backw&G['amplitud>=20'],y)
print('\nUltima fecha del sample y estado hoy (2026-11-06) de las condiciones:')
last=G.index.max(); print(last.date(), {c:bool(G.loc[last,c].any()) for c in ['backw','vvix_hi','macd12_26_9_ext','amplitud>=20','amplitud>=35']})
