import pandas as pd, numpy as np, sys
sp=sys.argv[1]; H=pd.read_pickle(sp+'/intra_h1.pkl'); Y=pd.read_pickle(sp+'/yf.pkl'); L=pd.read_pickle(sp+'/L.pkl')
rows=[]
for t in L.tk.unique():
    h=H[t].dropna(subset=['Close']).copy(); h['day']=h.index.tz_localize(None).normalize() if h.index.tz is None else h.index.tz_convert('America/New_York').tz_localize(None).normalize()
    h['n']=h.groupby('day').cumcount(); dd=Y[t].dropna(subset=['Close'])
    days=sorted(h.day.unique())
    for i,d in enumerate(days):
        b=h[h.day==d]
        if len(b)<6: continue
        lo=b.Low.min(); hi=b.High.max(); cl=b.Close.iloc[-1]; op=b.Open.iloc[0]; rng=hi-lo
        if rng<=0: continue
        li=int(np.argmin(b.Low.values)); tot=b.Volume.sum()
        f=dict(tk=t,day=d,low_idx=li,low_frac=li/(len(b)-1),close_pos=(cl-lo)/rng,
               last_up=cl>b.Close.iloc[-2], last2_up=cl>b.Open.iloc[-2],
               held=b.Low.iloc[-3:].min()>lo, held2=b.Low.iloc[-2:].min()>lo,
               first_vol=b.Volume.iloc[0]/tot if tot>0 else np.nan, last2_vol=b.Volume.iloc[-2:].sum()/tot if tot>0 else np.nan,
               bounce=(cl-lo), o2c=cl/op-1, lo=lo, hi=hi)
        # dia siguiente
        if i+1<len(days):
            nb=h[h.day==days[i+1]]
            if len(nb)>=4:
                f['n_low3']=nb.Low.iloc[:3].min(); f['n_px3']=nb.Close.iloc[2]; f['n_open']=nb.Open.iloc[0]
        rows.append(f)
I=pd.DataFrame(rows).set_index('day'); I.index.name='day'
I['key']=I.tk+I.index.strftime('%Y%m%d')
L=L.reset_index(); L['key']=L.tk+L.day.dt.strftime('%Y%m%d'); L=L.set_index('key')
J=L.join(I.set_index('key').drop(columns='tk'),how='inner').reset_index()
# minimos futuros diarios para "no se pierde el minimo de t"
parts=[]
for t,g in J.groupby('tk'):
    d=Y[t]['Low'].dropna(); idx={x:i for i,x in enumerate(d.index)}; arr=d.values
    fm=[arr[idx[x]+2:idx[x]+12].min() if x in idx and idx[x]+12<=len(arr) else np.nan for x in g.day]   # t+2..t+11 (tras D+1)
    fm1=[arr[idx[x]+1:idx[x]+11].min() if x in idx and idx[x]+11<=len(arr) else np.nan for x in g.day]
    g=g.assign(fmin2=fm,fmin1=fm1); parts.append(g)
J=pd.concat(parts); J.to_pickle(sp+'/J.pkl'); print(len(J),J.day.min(),J.day.max(),(J.score>=3).sum(),(J.score>=2).sum())
