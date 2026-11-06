import pandas as pd, numpy as np, sys
G=pd.read_pickle(sys.argv[1]+'/CDS.pkl'); G=G[G['win1.5'].notna()]
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']; G['score']=G[fam].sum(axis=1)
IS=G.index<'2019-01-01'
base=lambda col,m=slice(None): G.loc[m,col].mean()
bI,bO=G[IS]['win1.5'].mean(),G[~IS]['win1.5'].mean()
print('BASE todos los dias: win1.5=%.1f%% (IS %.1f / OOS %.1f) lose1.5=%.1f%% touch1.5=%.1f%% mfe=%.2f ATR r5=%.2f r10=%.2f ATR (%.2f%%)'%(G['win1.5'].mean()*100,bI*100,bO*100,G['lose1.5'].mean()*100,G['touch1.5'].mean()*100,G.mfe.mean(),G.r5a.mean(),G.r10a.mean(),G.r10p.mean()*100))
sigs=[c for c in G.columns if G[c].dtype==bool and c not in('spy_up','trend','bounce1')]
sigs+= ['score>=2','score>=3','score>=4']
G['score>=2']=G.score>=2; G['score>=3']=G.score>=3; G['score>=4']=G.score>=4
G['score>=3 & rebote>=1ATR']=(G.score>=3)&G.bounce1; sigs.append('score>=3 & rebote>=1ATR')
G['score>=3 & SPY>SMA200']=(G.score>=3)&G.spy_up; sigs.append('score>=3 & SPY>SMA200')
G['kl_in & trend']=G.kl_in&G.trend; sigs.append('kl_in & trend')
G['kl_in & SPY>SMA200']=G.kl_in&G.spy_up; sigs.append('kl_in & SPY>SMA200')
rows=[]
for c in sigs:
    m=G[c].values
    if m.sum()<300: continue
    y=G[m]; a=y[y.index<'2019-01-01']; b=y[y.index>='2019-01-01']
    if len(a)<100 or len(b)<100: continue
    rows.append(dict(senal=c.replace('n_',''),n=len(y),win=y['win1.5'].mean()*100,winIS=a['win1.5'].mean()*100,winOOS=b['win1.5'].mean()*100,
      edgeIS=(a['win1.5'].mean()-bI)*100,edgeOOS=(b['win1.5'].mean()-bO)*100,lose=y['lose1.5'].mean()*100,touch=y['touch1.5'].mean()*100,mfe=y.mfe.mean(),r10=y.r10a.mean(),r10pct=y.r10p.mean()*100))
R=pd.DataFrame(rows); R['edge_min']=R[['edgeIS','edgeOOS']].min(axis=1)
pd.set_option('display.width',250); pd.set_option('display.max_colwidth',48)
print('\nTOP por edge minimo (IS/OOS) en win1.5 (sube +1.5ATR antes que bajar -1.5ATR en 10 sesiones)')
print(R.sort_values('edge_min',ascending=False).head(18).round(1).drop(columns='edge_min').to_string(index=False))
print('\nPEORES'); print(R.sort_values('edge_min').head(6).round(1).drop(columns='edge_min').to_string(index=False))
print('\nsenales evaluadas:',len(R),' con edge_min>0:',(R.edge_min>0).sum(),' con edge_min>=2pts:',(R.edge_min>=2).sum())
