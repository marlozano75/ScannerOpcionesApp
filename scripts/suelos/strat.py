import pandas as pd, numpy as np, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); G=pd.read_pickle(sp+'/CDS.pkl'); A=pd.read_pickle(sp+'/A.pkl')
def running(df,key):
    """barra en curso del periodo (semana/mes) hasta cada dia, y barra anterior completa"""
    g=df.groupby(key)
    o=g.Open.transform('first'); h=g.High.cummax(); l=g.Low.cummin(); c=df.Close
    per=df.groupby(key).agg(H=('High','max'),L=('Low','min')); per['pH']=per.H.shift(); per['pL']=per.L.shift()
    pH=df[key].map(per.pH) if False else None
    return o,h,l,c
rows=[]
for t in sorted(G.tk.unique()):
    x=Y[t].dropna(subset=['Open','High','Low','Close']).copy(); x=x[x.Volume>0]
    o,h,l,c=x.Open,x.High,x.Low,x.Close; ph,pl=h.shift(),l.shift()
    up=(h>ph)&(l>=pl); dn=(l<pl)&(h<=ph); out=(h>ph)&(l<pl); ins=(h<=ph)&(l>=pl)
    F=pd.DataFrame(index=x.index)
    F['s_2D2U']=dn.shift()&up
    F['s_2D2D2U']=dn.shift()&dn.shift(2)&up
    F['s_3_2U']=out.shift()&up
    F['s_3_1_2U']=out.shift(2)&ins.shift()&up
    F['s_2D_1_2U']=dn.shift(2)&ins.shift()&up
    F['s_2U_1_2U']=up.shift(2)&ins.shift()&up
    F['s_1_2U']=ins.shift()&up
    F['s_1_1_2U']=ins.shift()&ins.shift(2)&up
    F['s_2U2U']=up.shift()&up
    F['s_2U2U2U']=up.shift()&up.shift(2)&up
    F['s_failed2D']=(l<pl)&(c>pl)&(c>o)           # 2D fallido y vela alcista
    F['s_failed2D_2U']=F['s_failed2D'].shift()&up
    F['s_hammer1']=ins&((np.minimum(o,c)-l)>=2*(c-o).abs())&(c>o)
    F['s_rev_2U2D_ctrl']=up.shift()&dn          # control bajista
    # continuidad entre temporalidades (barra en curso de semana y mes)
    for nm,key in (('w',x.index.to_period('W-FRI')),('m',x.index.to_period('M'))):
        k=pd.Series(key.astype(str),index=x.index); gb=x.groupby(k)
        wo=gb.Open.transform('first'); wh=gb.High.cummax(); wl=gb.Low.cummin()
        per=x.groupby(k).agg(H=('High','max'),L=('Low','min'),first=('Open','first'),last=('Close','last')); order=per.index.tolist()
        pH=per.H.shift(); pL=per.L.shift()
        pHh=k.map(pH); pLl=k.map(pL)
        F[f'{nm}_green']=(c>wo).values; F[f'{nm}_2U']=((wh>pHh)&(wl>=pLl)).values; F[f'{nm}_3']=((wh>pHh)&(wl<pLl)).values
        F[f'{nm}_2D']=((wl<pLl)&(wh<=pHh)).values
    F['d_green']=c>o
    F['FTFC_up']=F.d_green&F.w_green&F.m_green
    F['FTFC_dw']=(~F.d_green)&(~F.w_green)&(~F.m_green)
    F['dw_green_m_green']=F.d_green&F.w_green
    F['tk']=t; rows.append(F)
S=pd.concat(rows); S.index.name='day'
for c_ in S.columns:
    if c_!='tk': S[c_]=S[c_].fillna(False).astype(bool)
S.to_pickle(sp+'/STRAT.pkl'); print(S.shape)
