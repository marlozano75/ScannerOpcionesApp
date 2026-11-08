"""Ronda R10: patrones de suelo de Bulkowski (doble suelo y HCH invertido) con cierre confirmado sobre la clavicula,
gap en la ruptura y contexto de mercado; solos y combinados con las mejores señales anteriores.
Uso: python patrones.py <carpeta con yf.pkl, B2.pkl, CDS.pkl, A.pkl, FV.pkl>"""
import pandas as pd, numpy as np, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); G=pd.read_pickle(sp+'/CDS.pkl'); A=pd.read_pickle(sp+'/A.pkl'); B=pd.read_pickle(sp+'/B2.pkl')
K=5  # un minimo es pivote si es el menor de ±5 sesiones; se conoce 5 sesiones despues (sin mirar al futuro)
def pivots(l):
    v=l.values; n=len(v); out=[]
    for i in range(K,n-K):
        if v[i]==v[i-K:i+K+1].min() and v[i]<v[i-K:i].min()+1e-12: out.append(i)
    return out
def find(x,atr):
    o,h,l,c=[x[k].values for k in('Open','High','Low','Close')]; a=atr.values; n=len(c); piv=pivots(x.Low)
    sig=[]  # (indice, tipo, lo_dato)
    for a_i in range(len(piv)):
        for b_i in range(a_i+1,len(piv)):
            p1,p2=piv[a_i],piv[b_i]
            if p2-p1>60: break
            if p2-p1<8: continue
            L1,L2=l[p1],l[p2]
            if abs(L1/L2-1)>0.04: continue                   # dos suelos a la par (±4 %)
            neck=h[p1:p2+1].max()
            if (neck-max(L1,L2))<2*a[p2] or np.isnan(a[p2]): continue   # el pico intermedio debe pesar >=2 ATR
            conf=p2+K
            for t in range(conf,min(conf+30,n-1)):
                if c[t]<min(L1,L2)*0.99: break              # suelo roto -> patron anulado
                if c[t]>neck and c[t-1]<=neck:
                    sig.append((t,'DS',int(o[t]>h[t-1]))); break
    # HCH invertido: tres suelos, el central mas bajo
    for a_i in range(len(piv)-2):
        p1,p2,p3=piv[a_i],piv[a_i+1],piv[a_i+2]
        if p3-p1>90 or p2-p1<5 or p3-p2<5: continue
        Lh=l[p2]
        if not(Lh<l[p1] and Lh<l[p3]): continue
        if abs(l[p1]/l[p3]-1)>0.06: continue                # hombros a similar altura
        if np.isnan(a[p3]) or min(l[p1],l[p3])-Lh<0.5*a[p3]: continue
        neck=max(h[p1:p2+1].max(),h[p2:p3+1].max())
        conf=p3+K
        for t in range(conf,min(conf+30,n-1)):
            if c[t]<Lh*0.99: break
            if c[t]>neck and c[t-1]<=neck:
                sig.append((t,'HCH',int(o[t]>h[t-1]))); break
    return sig
rows=[]
for t in sorted(G.tk.unique()):
    x=Y[t].dropna(subset=['Open','High','Low','Close']); x=x[x.Volume>0]
    atr=B[B.tk==t].atr.reindex(x.index)
    for i,ty,gap in find(x,atr): rows.append((x.index[i],t,ty,gap))
S=pd.DataFrame(rows,columns=['day','tk','tipo','gap']).drop_duplicates(['day','tk','tipo'])
print('señales brutas',len(S),S.tipo.value_counts().to_dict())
mk=['backw','vvix_hi','spy_osold','vix_spike']
M=A[mk].groupby(level=0).first()
G=G.join(M,how='left'); G[mk]=G[mk].fillna(False).astype(bool)
G=G[G.r10p.notna()].reset_index()
spy=Y['SPY']['Close'].dropna() if 'SPY' in Y.columns.get_level_values(0) else None
G['bull']=G['spy_up'].astype(bool) if 'spy_up' in G else False
G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']; G['w']=G['win1.5']; G['l']=G['lose1.5']
S=S.merge(G,on=['day','tk'],how='inner')
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']; S['score']=S[fam].sum(axis=1)
G['score']=G[fam].sum(axis=1)
def row(nm,z,base):
    if len(z)<25: print(f'{nm:58s} n={len(z)} (pocos)'); return
    d=z.day.nunique(); ds=pd.Series(sorted(z.day.unique())); ep=int((ds.diff().dt.days.fillna(99)>10).sum())
    byd=z.groupby('day').y1.mean().mean()
    a=z[z.day<'2019-01-01']; o=z[z.day>='2019-01-01']
    print(f'{nm:58s} n={len(z):4d} dias={d:4d} ep={ep:3d} P(cierre>ent)={z.y1.mean()*100:5.1f}% (dia {byd*100:4.1f}%, IS {a.y1.mean()*100:4.1f}/OOS {o.y1.mean()*100:4.1f}) toca+1.5ATR={z.y2.mean()*100:5.1f}% gana={z.w.mean()*100:4.1f}% pierde={z.l.mean()*100:4.1f}% r10={z.r10p.mean()*100:5.2f}%')
print('BASE todos los dias: P(cierre>ent)=%.1f%% toca=%.1f%% gana=%.1f%% pierde=%.1f%%'%(G.y1.mean()*100,G.y2.mean()*100,G.w.mean()*100,G.l.mean()*100))
for ty in ('DS','HCH'):
    z=S[S.tipo==ty]
    row(f'{ty} confirmado (cierre>clavicula)',z,G)
    row(f'{ty} + gap alcista en la ruptura',z[z.gap==1],G)
    row(f'{ty} sin gap',z[z.gap==0],G)
    row(f'{ty} + SPY>SMA200 (mercado alcista)',z[z.bull],G)
    row(f'{ty} + SPY<SMA200 (mercado bajista)',z[~z.bull],G)
    row(f'{ty} + backwardation VIX>VIX3M',z[z.backw],G)
    row(f'{ty} + amplitud>=20%',z[z.breadth>=.2],G)
    row(f'{ty} + score>=3',z[z.score>=3],G)
    row(f'{ty} + vvix alto',z[z.vvix_hi],G)
z=S.drop_duplicates(['day','tk'])
row('DS o HCH',z,G); row('DS o HCH + gap',z[z.gap==1],G); row('DS o HCH + backw',z[z.backw],G)
# ---- combinacion con la mejor señal previa (pullback RSI2<10 en tendencia + backw) ----
pb='n_pullback: RSI2<10 en tendencia'
print('\nSeñal previa: pullback RSI2<10 en tendencia & backw')
row('previa sola (todas las filas)',G[G[pb]&G.backw],G)
# patron reciente (<=10 sesiones) + previa: ¿se suman?
Sp=S.drop_duplicates(['day','tk'])[['day','tk']].assign(pat=1)
G2=G.merge(Sp,on=['day','tk'],how='left'); G2['pat']=G2.pat.fillna(0).astype(bool)
G2=G2.sort_values(['tk','day']); G2['pat10']=G2.groupby('tk').pat.transform(lambda s:s.rolling(10,min_periods=1).max()).astype(bool)
row('previa & patron en las ultimas 10 sesiones',G2[G2[pb]&G2.backw&G2.pat10],G)
row('patron hoy & pullback RSI2<10 (ultimas 10 ses.)',G2[G2.pat&(G2.groupby('tk')[pb].transform(lambda s:s.rolling(10,min_periods=1).max())>0)],G)
row('patron hoy & macd ext (10 ses.)',G2[G2.pat&(G2.groupby('tk')['macd12_26_9_ext'].transform(lambda s:s.rolling(10,min_periods=1).max())>0)],G)
row('patron hoy & backw',G2[G2.pat&G2.backw],G)
row('patron hoy & backw & amplitud>=20%',G2[G2.pat&G2.backw&(G2.breadth>=.2)],G)
