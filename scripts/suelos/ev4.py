import pandas as pd, numpy as np, sys, re
A=pd.read_pickle(sys.argv[1]+'/B.pkl'); L=A[A.lab.notna()]
tr=(L.index<'2019-01-01'); te=~tr; bi,bo=L[tr].lab.mean(),L[te].lab.mean()
sig=[c for c in A.columns if A[c].dtype==bool and c not in('ok','kl_below')]
rows=[]
for c in sig:
    m=L[c].values
    if m.sum()<150: continue
    y=L[m]; a=y[y.index<'2019-01-01']; b=y[y.index>='2019-01-01']
    rows.append(dict(s=c,n=len(y),prec=y.lab.mean()*100,hoy=y.lab0.mean()*100,liftIS=a.lab.mean()/bi if len(a)>30 else np.nan,liftOOS=b.lab.mean()/bo if len(b)>30 else np.nan,r10=y.r10.mean()*100,win=(y.r10>0).mean()*100,nl10=y.nl10.mean()*100,mae=y.mae.mean()*100))
R=pd.DataFrame(rows); R['fam']=R.s.str.extract(r'^(dA|dL|mog|moc|macd)')[0].fillna('otro')
pd.set_option('display.width',220)
print('base',round(L.lab.mean()*100,1),'r10',round(L.r10.mean()*100,2),'nl10',round(L.nl10.mean()*100,1))
for f,g in R.groupby('fam'):
    print('\n==',f,'(top 8 por min(liftIS,liftOOS) con n>=300)')
    g=g[g.n>=300].copy(); g['m']=g[['liftIS','liftOOS']].min(axis=1); print(g.sort_values('m',ascending=False).head(8).drop(columns=['fam','m']).round(1).to_string(index=False))
