import numpy as np, pandas as pd, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); G=pd.read_pickle(sp+'/CDS.pkl')
NB=40
def vprofile(lo,hi,vol):
    a,b=lo.min(),hi.max()
    if not b>a: return None
    e=np.linspace(a,b,NB+1); rng=np.maximum(hi-lo,1e-9)
    ov=np.clip(np.minimum(hi[:,None],e[None,1:])-np.maximum(lo[:,None],e[None,:-1]),0,None)/rng[:,None]*vol[:,None]
    p=ov.sum(0); 
    if p.sum()<=0: return None
    c=(e[:-1]+e[1:])/2; i=int(p.argmax()); tot=p.sum(); acc=p[i]; l=r=i
    while acc<0.7*tot and (l>0 or r<NB-1):
        up=p[r+1] if r<NB-1 else -1; dn=p[l-1] if l>0 else -1
        if up>=dn: r+=1; acc+=p[r]
        else: l-=1; acc+=p[l]
    return c[i],e[l],e[r+1],c,p      # POC, VAL, VAH
out=[]
for t in sorted(G.tk.unique()):
    x=Y[t].dropna(subset=['Close','High','Low','Open']); x=x[x.Volume>0]
    o,h,l,c,v=[x[k].values for k in ('Open','High','Low','Close','Volume')]; n=len(x)
    atr=pd.Series(np.maximum.reduce([h-l,np.abs(h-np.r_[c[0],c[:-1]]),np.abs(l-np.r_[c[0],c[:-1]])])).ewm(alpha=1/14,adjust=False).mean().values
    dper=(c-o)/o*100; thr=np.cumsum(np.abs(dper))/np.arange(1,n+1)*2
    F={k:np.zeros(n,bool) for k in ['fvg_new_bull','fvg_new_bear','fvg_retest_bull','fvg_retest_bull_up','fvg_mit_bear','fvg_in_bull','fvg_retest_2','fvg_new_bull_big']}
    nb_near=np.zeros(n)
    bull=[];bear=[]      # (top,bottom,t0)
    for i in range(n):
        # mitigacion y señales con zonas abiertas ANTES de hoy
        bull=[z for z in bull if i-z[2]<=120]; bear=[z for z in bear if i-z[2]<=120]
        keep=[]
        for z in bull:
            if c[i]<z[1]: continue           # mitigada: cierre bajo el fondo
            if i-z[2]>=2 and l[i]<=z[0]:     # toque de la zona
                F['fvg_retest_bull'][i]=True
                if c[i]>o[i]: F['fvg_retest_bull_up'][i]=True
            if z[1]<=c[i]<=z[0]: F['fvg_in_bull'][i]=True
            if 0<=c[i]-z[0]<=atr[i]: nb_near[i]+=1
            keep.append(z)
        bull=keep; keep=[]
        for z in bear:
            if c[i]>z[0]:
                if i-z[2]>=2: F['fvg_mit_bear'][i]=True
                continue
            keep.append(z)
        bear=keep
        if i>=2:
            if l[i]>h[i-2] and c[i-1]>h[i-2] and dper[i-1]>thr[i]:
                F['fvg_new_bull'][i]=True; bull.append((l[i],h[i-2],i))
                if (l[i]-h[i-2])>atr[i]*0.5: F['fvg_new_bull_big'][i]=True
            if h[i]<l[i-2] and c[i-1]<l[i-2] and -dper[i-1]>thr[i]:
                F['fvg_new_bear'][i]=True; bear.append((l[i-2],h[i],i))
    F['fvg_retest_2']=F['fvg_retest_bull']&(nb_near>=1)
    D=pd.DataFrame(F,index=x.index); D['fvg_near_bull']=nb_near>=1
    # volume profile
    for W in (60,120):
        poc=np.full(n,np.nan);val=poc.copy();vah=poc.copy()
        for i in range(W,n):
            r=vprofile(l[i-W:i],h[i-W:i],v[i-W:i])      # perfil con barras hasta ayer
            if r: poc[i],val[i],vah[i]=r[0],r[1],r[2]
        s=lambda a: np.r_[np.nan,a[:-1]]
        pc=s(c)
        D[f'vp{W}_reclaim_val']=(pc<val)&(c>val)
        D[f'vp{W}_touch_val_hold']=(l<=val)&(c>val)&(pc>=val)
        D[f'vp{W}_cross_poc_up']=(pc<poc)&(c>poc)
        D[f'vp{W}_poc_support']=(l<=poc)&(c>poc)&(pc>poc)
        D[f'vp{W}_break_vah']=(pc<=vah)&(c>vah)
        D[f'vp{W}_below_val']=c<val
        D[f'vp{W}_far_below_poc_1.5']=(poc-c)>=1.5*atr
        D[f'vp{W}_far_below_poc_1.5_rev']=((poc-c)>=1.5*atr)&(c>o)&(c>pc)
        D[f'vp{W}_in_va_above_poc']=(c>poc)&(c<vah)
    D['tk']=t; out.append(D)
A=pd.concat(out); A.index.name='day'
A.to_pickle(sp+'/FV.pkl'); print(A.shape)
