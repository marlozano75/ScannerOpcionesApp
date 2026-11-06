import pandas as pd, numpy as np, sys
J=pd.read_pickle(sys.argv[1]+'/J.pkl')
J['nobreak']=(J.fmin1>=J.lo).where(J.fmin1.notna())
J['early']=J.low_idx<=2; J['late']=J.low_idx>=5
J['vrev']=J.early&(J.close_pos>=0.7); J['cp_hi']=J.close_pos>=0.7; J['cp_lo']=J.close_pos<=0.3
J['bounce1atr']=J.bounce>=J.atr*1.0; J['bounce15']=J.bounce>=J.atr*1.5
J['heavy_last']=J.last2_vol>=J.last2_vol.median(); J['heavy_first']=J.first_vol>=J.first_vol.median()
J['n_conf']=(J.n_low3>J.lo)&(J.n_px3>J.close)         # D+1 hasta 11:30: no pierde el minimo y esta sobre el cierre de t
J['n_gapup']=J.n_open>J.close
def wil(k,n,z=1.96):
    if n==0: return (np.nan,np.nan)
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); a=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)); return ((c-a)/d*100,(c+a)/d*100)
feats=['early','late','vrev','cp_hi','cp_lo','last_up','last2_up','held','held2','bounce1atr','bounce15','heavy_last','heavy_first']
def tab(m,title,target='lab0',fs=feats):
    D=J[m&J[target].notna()]
    print(f'\n== {title}: n={len(D)}  P({target})={D[target].mean()*100:.1f}%  P(nobreak10)={D.nobreak.mean()*100:.1f}%  r10={D.r10.mean()*100:.2f}%')
    out=[]
    for f in fs:
        a=D[D[f]==True]; b=D[D[f]==False]
        if len(a)<25 or len(b)<25: continue
        lo,hi=wil(a[target].sum(),len(a))
        out.append((f,len(a),a[target].mean()*100,lo,hi,b[target].mean()*100,a.nobreak.mean()*100,b.nobreak.mean()*100,a.r10.mean()*100))
    print(pd.DataFrame(out,columns=['feat','n_si','P_si','ic_lo','ic_hi','P_no','nobreak_si','nobreak_no','r10_si']).round(1).sort_values('P_si',ascending=False).to_string(index=False))
for s in (2,3):
    tab(J.score>=s,f'score>={s} (dia t, 1h)','lab0')
# confirmacion D+1 intradia
for s in (2,3):
    m=(J.score>=s)&J.n_conf.notna()
    tab(m,f'score>={s} + confirmacion D+1 (hasta 11:30)','lab0',['n_conf','n_gapup','vrev','held','cp_hi','early'])
