import pandas as pd, numpy as np, sys
sp=sys.argv[1]; Y=pd.read_pickle(sp+'/yf.pkl'); B=pd.read_pickle(sp+'/B2.pkl')
rows=[]
for t in sorted(B.tk.unique()):
    x=Y[t].dropna(subset=['Open','High','Low','Close']); x=x[x.Volume>0]; o,h,l,c=x.Open,x.High,x.Low,x.Close
    atr=B[B.tk==t].atr.reindex(x.index)
    ph,pl=h.shift(),l.shift(); up=(h>ph)&(l>=pl); dn=(l<pl)&(h<=ph); out=(h>ph)&(l<pl); ins=(h<=ph)&(l>=pl)
    # señal en t (vela interior u otra), disparo en t+1 al romper el maximo de t
    trig_up=h.shift(-1)>h; trig_dn=l.shift(-1)<l          # ruptura arriba / abajo al dia siguiente
    clean=trig_up&~trig_dn                                  # solo rompe arriba (no outside)
    E=np.maximum(h,o.shift(-1))                             # buy stop en el maximo (si abre por encima, a la apertura)
    HZ=10
    Hm=np.column_stack([h.shift(-i).values for i in range(1,HZ+2)]); Lm=np.column_stack([l.shift(-i).values for i in range(1,HZ+2)]); Cm=np.column_stack([c.shift(-i).values for i in range(1,HZ+2)])
    a=atr.values; e=E.values
    up_t=(e+1.5*a)[:,None]; dn_t=(e-1.5*a)[:,None]
    hu=Hm[:,1:]>=up_t; hd=Lm[:,1:]<=dn_t                    # a partir del dia de disparo +1 (t+2..t+11); el dia de disparo no se cuenta (orden intradia desconocido)
    fu=np.where(hu.any(1),hu.argmax(1),99); fd=np.where(hd.any(1),hd.argmax(1),99)
    D=pd.DataFrame(index=x.index)
    D['trig']=clean.values; D['ent']=e; D['win']=(fu<fd).astype(float); D['lose']=((fd<=fu)&(fd<99)).astype(float); D['touch']=hu.any(1).astype(float)
    D['r10']=Cm[:,HZ]/e-1                                   # cierre del dia 10 tras la señal vs entrada
    D['pos']=(Cm[:,HZ]>e).astype(float)
    D['tk']=t
    for nm,s in (('1 (cualquier vela interior)',ins),('2D-1',ins&dn.shift()),('3-1',ins&out.shift()),('2U-1',ins&up.shift()),('1-1',ins&ins.shift()),
                 ('2D-2D-1',ins&dn.shift()&dn.shift(2)),('2U-2U-1',ins&up.shift()&up.shift(2)),
                 ('2D-2U (2U de giro: disparo = ruptura del maximo)',up&dn.shift()),('martillo/2D fallido',(l<pl)&(c>pl)&(c>o))):
        D['p_'+nm]=s.fillna(False).values
    ok=~np.isnan(Cm).any(1)&~np.isnan(a)&~np.isnan(e); D=D[ok]; rows.append(D)
R=pd.concat(rows); R['tr']=R.trig
base=R[R.tr]; IS=R.index<'2019-01-01'
print('BASE (cualquier dia, entrando en la ruptura del maximo de ayer cuando ocurre): n=%d P(cierre d10 > entrada)=%.1f%% touch+1.5ATR=%.1f%% win=%.1f%% lose=%.1f%% r10=%.2f%%'%(len(base),base.pos.mean()*100,base.touch.mean()*100,base.win.mean()*100,base.lose.mean()*100,base.r10.mean()*100))
for c_ in [c for c in R.columns if c.startswith('p_')]:
    z=R[R[c_]&R.tr]; n_all=R[c_].sum()
    if len(z)<80: continue
    a=z[z.index<'2019-01-01']; o=z[z.index>='2019-01-01']
    print(f'{c_[2:]:50s} señales={n_all:6d} disparan arriba={len(z):6d} P(cierre>ent)={z.pos.mean()*100:5.1f}% (IS {a.pos.mean()*100:4.1f}/OOS {o.pos.mean()*100:4.1f}) touch={z.touch.mean()*100:5.1f}% win={z.win.mean()*100:5.1f}% lose={z.lose.mean()*100:4.1f}% r10={z.r10.mean()*100:5.2f}%')
