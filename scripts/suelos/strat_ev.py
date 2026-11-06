import pandas as pd, numpy as np, sys
sp=sys.argv[1]; G=pd.read_pickle(sp+'/CDS.pkl'); S=pd.read_pickle(sp+'/STRAT.pkl'); Y=pd.read_pickle(sp+'/yf.pkl')
vix,v3=Y['^VIX']['Close'],Y['^VIX3M']['Close']; backw=((vix/v3)>1)
G=G.reset_index().merge(S.reset_index(),on=['day','tk'],how='inner').set_index('day').sort_index()
G['backw']=G.index.map(backw).fillna(False).astype(bool)
G=G[G.r10p.notna()]; G['y1']=(G.r10p>0).astype(float)
IS=G.index<'2019-01-01'
print('BASE n=%d  P(r10>0)=%.1f%%  touch1.5=%.1f%%  win1.5=%.1f%%  suelo=%.1f%%'%(len(G),G.y1.mean()*100,G['touch1.5'].mean()*100,G['win1.5'].mean()*100,G.lab.mean()*100))
def rep(nm,m,show=True):
    z=G[m]; 
    if len(z)<80: return
    a=z[z.index<'2019-01-01']; o=z[z.index>='2019-01-01']
    print(f'{nm:48s} n={len(z):6d} P(r10>0)={z.y1.mean()*100:5.1f}% (IS {a.y1.mean()*100:4.1f}/OOS {o.y1.mean()*100:4.1f}) touch1.5={z["touch1.5"].mean()*100:5.1f}% win={z["win1.5"].mean()*100:5.1f}% lose={z["lose1.5"].mean()*100:4.1f}% r10={z.r10p.mean()*100:5.2f}% suelo={z.lab.mean()*100:5.1f}%')
pats=[c for c in S.columns if c.startswith('s_')]
print('\n--- patrones Strat solos'); 
for p in pats: rep(p,G[p])
print('\n--- continuidad entre temporalidades')
for c in ['FTFC_up','FTFC_dw','dw_green_m_green','w_2U','w_3','w_2D','m_2U','m_2D']: rep(c,G[c])
print('\n--- patrones alcistas x FTFC_up / semana verde')
for p in ['s_2D2U','s_2D2D2U','s_3_2U','s_3_1_2U','s_2D_1_2U','s_2U_1_2U','s_1_2U','s_2U2U','s_failed2D_2U']:
    rep(p+' & FTFC_up',G[p]&G.FTFC_up); rep(p+' & semana 2U',G[p]&G.w_2U)
print('\n--- con estres (VIX>VIX3M) o sobreventa')
for p in ['s_2D2U','s_3_2U','s_1_2U','s_failed2D']:
    rep(p+' & backw',G[p]&G.backw); rep(p+' & tendencia',G[p]&G.trend); rep(p+' & RSI<30 ayer(f_rsi)',G[p]&G.f_rsi)
rep('2D2U & Bollinger ayer (d2u_pb)',G.d2u_pb)
rep('FTFC_up & backw',G.FTFC_up&G.backw); rep('FTFC_up & tendencia',G.FTFC_up&G.trend)
rep('2D2U | 3_2U | 1_2U (cualquier 2U de giro) & backw',(G.s_2D2U|G.s_3_2U|G.s_1_2U)&G.backw)
