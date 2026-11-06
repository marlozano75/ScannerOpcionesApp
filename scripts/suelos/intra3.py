import pandas as pd, numpy as np, sys
sp=sys.argv[1]; J=pd.read_pickle(sp+'/J.pkl'); Y=pd.read_pickle(sp+'/yf.pkl'); L=pd.read_pickle(sp+'/L.pkl')
J['nobreak']=(J.fmin1>=J.lo).where(J.fmin1.notna()); J['bounce1atr']=J.bounce>=J.atr
J['early']=J.low_idx<=2; J['held']=J.held
J['n_conf']=(J.n_low3>J.lo)&(J.n_px3>J.close)
def r(m,t,tg='lab0'):
    D=J[m&J[tg].notna()]; print(f'{t:62s} n={len(D):5d} P(t=suelo)={D.lab0.mean()*100:5.1f}% nobreak10={D.nobreak.mean()*100:5.1f}% r10={D.r10.mean()*100:5.2f}% win={(D.r10>0).mean()*100:4.1f}%')
    return D
h1=J.day<'2025-06-01'; h2=~h1
print('--- mitades de la muestra intradia (2023-12..2025-05 / 2025-06..2026-10)')
for nm,hh in (('H1',h1),('H2',h2)):
    for t,m in (('score>=3',J.score>=3),('score>=3 & bounce>=1ATR',(J.score>=3)&J.bounce1atr),('score>=3 & n_conf(D+1 11:30)',(J.score>=3)&J.n_conf.fillna(False)),
                ('score>=3 & early&held',(J.score>=3)&J.early&J.held),('score>=3 & bounce & n_conf',(J.score>=3)&J.bounce1atr&J.n_conf.fillna(False))):
        r(m&hh,nm+' '+t)
print('\n--- combinaciones (toda la muestra intradia)')
for t,m in (('score>=2 & bounce>=1ATR & n_conf',(J.score>=2)&J.bounce1atr&J.n_conf.fillna(False)),('score>=3 & bounce>=1ATR & n_conf',(J.score>=3)&J.bounce1atr&J.n_conf.fillna(False)),
            ('score>=3 & early & held & n_conf',(J.score>=3)&J.early&J.held&J.n_conf.fillna(False)),('score>=3 & NOT early',(J.score>=3)&~J.early)):
    r(m,t)
# --- equivalente DIARIO de bounce en los 22 años (sin intradia)
L=L[L.lab.notna()].copy()
parts=[]
for t,g in L.groupby('tk'):
    x=Y[t].reindex(g.index); parts.append(g.assign(dlow=x.Low.values,dclose=x.Close.values))
L=pd.concat(parts); L["b1"]=(L.dclose-L.dlow)>=L.atr
print('\n--- diario 2004-2026: rebote cierre-minimo >=1 ATR (sin intradia)')
for s in (2,3,4):
    for nm,m in ((f'score>={s}',L.score>=s),(f'score>={s} & rebote>=1ATR',(L.score>=s)&L.b1),(f'score>={s} & rebote<1ATR',(L.score>=s)&~L.b1)):
        D=L[m]; a=D[D.index<'2019']; b=D[D.index>='2019']; print(f'{nm:35s} n={len(D):6d} P(hoy|ayer)={D.lab.mean()*100:5.1f}% (IS {a.lab.mean()*100:4.1f}/OOS {b.lab.mean()*100:4.1f}) P(t=suelo)={D.lab0.mean()*100:5.1f}% nl10={D.nl10.mean()*100:5.1f}% r10={D.r10.mean()*100:5.2f}%')
