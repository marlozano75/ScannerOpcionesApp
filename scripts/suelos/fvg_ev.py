import pandas as pd, numpy as np, sys
sp=sys.argv[1]; G=pd.read_pickle(sp+'/CDS.pkl'); F=pd.read_pickle(sp+'/FV.pkl')
G=G.reset_index().merge(F.reset_index(),on=['day','tk'],how='inner').set_index('day').sort_index()
G=G[G['win1.5'].notna()]
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']; G['score']=G[fam].sum(axis=1)
IS=G.index<'2019-01-01'; bI,bO,b=G[IS]['win1.5'].mean(),G[~IS]['win1.5'].mean(),G['win1.5'].mean()
Lb=G.lab.dropna().mean()
print(f'BASE win1.5={b*100:.1f}% (IS {bI*100:.1f}/OOS {bO*100:.1f}) lose={G["lose1.5"].mean()*100:.1f}% r10={G.r10p.mean()*100:.2f}% suelo(hoy|ayer)={Lb*100:.1f}% n={len(G)}')
cols=[c for c in F.columns if c!='tk' and F[c].dtype==bool]
rows=[]
for c in cols:
    m=G[c].values
    if m.sum()<150: continue
    y=G[m]; a=y[y.index<'2019-01-01']; o=y[y.index>='2019-01-01']
    if len(a)<50 or len(o)<50: continue
    rows.append(dict(senal=c,n=len(y),win=y['win1.5'].mean()*100,winIS=a['win1.5'].mean()*100,winOOS=o['win1.5'].mean()*100,lose=y['lose1.5'].mean()*100,
      r10=y.r10p.mean()*100,mfe=y.mfe.mean(),suelo=y.lab.mean()*100,liftsuelo=y.lab.mean()/Lb))
R=pd.DataFrame(rows).sort_values('win',ascending=False)
pd.set_option('display.width',230); print(R.round(1).to_string(index=False))
print('\n--- combinaciones con contexto')
def rep(nm,m):
    y=G[m]; a=y[y.index<'2019-01-01']; o=y[y.index>='2019-01-01']
    print(f'{nm:62s} n={len(y):6d} win={y["win1.5"].mean()*100:5.1f}% (IS {a["win1.5"].mean()*100:4.1f}/OOS {o["win1.5"].mean()*100:4.1f}) lose={y["lose1.5"].mean()*100:4.1f}% r10={y.r10p.mean()*100:5.2f}%')
rep('retest FVG alcista + tendencia',G.fvg_retest_bull&G.trend)
rep('retest FVG alcista (vela alcista) + tendencia',G.fvg_retest_bull_up&G.trend)
rep('retest FVG alcista + amplitud>=20%',G.fvg_retest_bull&G.f_brd)
rep('retest FVG alcista + score>=3',G.fvg_retest_bull&(G.score>=3))
rep('FVG alcista nuevo + tendencia',G.fvg_new_bull&G.trend)
rep('FVG alcista nuevo grande (>0.5 ATR)',G.fvg_new_bull_big)
rep('FVG bajista mitigado (cierre sobre su techo) + tendencia',G.fvg_mit_bear&G.trend)
for W in (60,120):
    rep(f'VP{W}: reclama VAL + tendencia',G[f'vp{W}_reclaim_val']&G.trend)
    rep(f'VP{W}: reclama VAL + amplitud>=20%',G[f'vp{W}_reclaim_val']&G.f_brd)
    rep(f'VP{W}: lejos bajo POC (>=1.5ATR) + giro + SPY>SMA200',G[f'vp{W}_far_below_poc_1.5_rev']&G.spy_up)
