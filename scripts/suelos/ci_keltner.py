"""Intervalo de confianza de la diferencia bajo/no bajo Keltner en el pullback RSI2<10 + VIX>VIX3M (2026-11-09).
Uso: python ci_keltner.py <carpeta con yf.pkl y CDS.pkl>"""
import pandas as pd, numpy as np, sys
sp=sys.argv[1]
G=pd.read_pickle(sp+'/CDS.pkl'); Y=pd.read_pickle(sp+'/yf.pkl')
v=Y['^VIX']['Close'].dropna(); v3=Y['^VIX3M']['Close'].dropna()
G['backw']=G.index.map(((v/v3)>1).fillna(False)).fillna(False).astype(bool)
G=G.sort_index(); G=G[G.r10p.notna()]; G['y1']=(G.r10p>0).astype(float); G['y2']=G['touch1.5']
z=G[G['n_pullback: RSI2<10 en tendencia']&G.backw].copy()
days=pd.Series(z.index.unique()).sort_values(); ep=(days.diff().dt.days.fillna(99)>10).cumsum().values
z['ep']=z.index.map(dict(zip(days,ep))); z['kb']=z.kl_below.astype(bool); z['d']=z.index
rng=np.random.default_rng(1)
for y in ('y2','y1'):
    print('\n##',y)
    # por episodio: P bajo y P no bajo dentro de cada episodio (pesando filas), solo episodios con ambos
    g=z.groupby(['ep','kb'])[y].mean().unstack(); both=g.dropna()
    diff=(both[True]-both[False]); print('episodios con ambos grupos:',len(both),'| diff media %.1f pts (mediana %.1f)'%(diff.mean()*100,diff.median()*100))
    bs=[diff.values[rng.integers(0,len(diff),len(diff))].mean() for _ in range(10000)]
    print('  IC95%% bootstrap pareado por episodio: %.1f a %.1f | P(diff<=0)=%.3f'%(np.percentile(bs,2.5)*100,np.percentile(bs,97.5)*100,(np.array(bs)<=0).mean()))
    # diferencia de P(ep) como en la tabla: episodios definidos en cada subconjunto (aprox: mismos episodios globales, media por episodio)
    Eb=z[z.kb].groupby('ep')[y].mean(); En=z[~z.kb].groupby('ep')[y].mean()
    obs=Eb.mean()-En.mean(); print('diferencia de P por episodio (episodios globales, no pareada): %.1f pts (n=%d vs %d)'%(obs*100,len(Eb),len(En)))
    bs=[]
    eps=z.ep.unique()
    gb=z.groupby('ep')
    P={e:(z[(z.ep==e)&z.kb][y].mean(),z[(z.ep==e)&~z.kb][y].mean()) for e in eps}
    arr=np.array([P[e] for e in eps])
    for _ in range(10000):
        s=arr[rng.integers(0,len(arr),len(arr))]; a=np.nanmean(s[:,0]); b=np.nanmean(s[:,1]); bs.append(a-b)
    print('  IC95%% bootstrap por episodios (remuestreo de los 82): %.1f a %.1f | P(diff<=0)=%.3f'%(np.nanpercentile(bs,2.5)*100,np.nanpercentile(bs,97.5)*100,(np.array(bs)<=0).mean()))
    # filas pooled con cluster bootstrap por episodio
    X=z[[ 'ep','kb',y]].copy(); idx={e:X[X.ep==e] for e in eps}
    bs=[]
    for _ in range(4000):
        s=pd.concat([idx[e] for e in rng.choice(eps,len(eps))]); bs.append(s[s.kb][y].mean()-s[~s.kb][y].mean())
    d0=z[z.kb][y].mean()-z[~z.kb][y].mean()
    print('filas: diff %.1f pts (%d vs %d) IC95%% bootstrap por cluster: %.1f a %.1f | P(diff<=0)=%.3f'%(d0*100,z.kb.sum(),(~z.kb).sum(),np.percentile(bs,2.5)*100,np.percentile(bs,97.5)*100,(np.array(bs)<=0).mean()))
    # permutacion dentro de episodio (barajar kb dentro de cada episodio)
    obs=d0; cnt=0; N=4000; kbv=z.kb.values; yv=z[y].values; ev=z.ep.values
    gi=[np.where(ev==e)[0] for e in eps]
    for _ in range(N):
        k=kbv.copy()
        for ii in gi: k[ii]=rng.permutation(kbv[ii])
        cnt+= (yv[k].mean()-yv[~k].mean())>=obs
    print('  test de permutacion dentro de cada episodio: p=%.4f'%(cnt/N))
    # por mitades
    for nm,mk in (('2004-18',z.d<'2019-01-01'),('2019-26',z.d>='2019-01-01')):
        w=z[mk]; print('  %s filas diff %.1f pts (%d vs %d)'%(nm,(w[w.kb][y].mean()-w[~w.kb][y].mean())*100,w.kb.sum(),(~w.kb).sum()))
