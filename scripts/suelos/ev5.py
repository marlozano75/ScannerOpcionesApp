import pandas as pd, numpy as np, sys
A=pd.read_pickle(sys.argv[1]+'/B.pkl')
g=A.groupby('tk')
def lag_or(c,n=2): return g[c].transform(lambda s: s.astype(float).rolling(n,min_periods=1).max()).astype(bool)
# control: nuevo minimo de 20d ayer y cierre al alza hoy (sin MACD)
cl=A.close
newlow_y=g['close'].transform(lambda s: (s.shift()<=s.shift(2).rolling(20).min()))
up=g['close'].transform(lambda s: s>s.shift())
A['ctrl_newlow_up']=newlow_y&up
B=pd.DataFrame(index=A.index)
B['osc']=lag_or('moc20_2_r')|lag_or('kl_in')|lag_or('mog10_14_2_r')       # reversion de banda de volatilidad (Keltner/Mogalef canal/Mogalef regresion)
B['atr']=lag_or('dA10_3')|lag_or('dL5_3')                                  # caida >=3 ATR (cierre vs max10d) o minimo vs max5d
B['macd']=lag_or('macd12_26_9_div_rev',1)|lag_or('macd8_17_9_div_rev',1)
B['strat']=A.d2u|A.d2u_pb
B['vol']=A.rv3>=2
B['brd']=A.breadth>=.2
B['rsi']=lag_or('pb0')
for c in B.columns: A['f_'+c]=B[c].values
A.to_pickle(sys.argv[1]+'/B2.pkl')
L=A[A.lab.notna()]; tr=L.index<'2019-01-01'
def rep(name,m):
    y=L[m.values]; a=y[y.index<'2019-01-01']; b=y[y.index>='2019-01-01']
    print(f'{name:55s} n={len(y):6d} prec={y.lab.mean()*100:5.1f} (IS {a.lab.mean()*100:4.1f} / OOS {b.lab.mean()*100:4.1f}) hoy={y.lab0.mean()*100:4.1f} r10={y.r10.mean()*100:5.2f} win={(y.r10>0).mean()*100:4.1f} nl10={y.nl10.mean()*100:4.1f}')
rep('CONTROL: nuevo min20d ayer + cierre sube',L.ctrl_newlow_up)
rep('MACD12 div_rev',L.macd12_26_9_div_rev); rep('MACD div_rev (cualquiera)',L.f_macd)
rep('MACD div_rev + caida ATR',L.f_macd&L.f_atr)
rep('MACD div_rev + banda',L.f_macd&L.f_osc)
rep('ctrl + caida ATR',L.ctrl_newlow_up&L.f_atr)
fam=['f_osc','f_atr','f_macd','f_strat','f_vol','f_brd','f_rsi']
L=L.assign(score=L[fam].sum(axis=1))
print()
for s in range(0,8):
    m=L.score==s
    if m.sum()>30: rep(f'score={s}',m)
for s in (2,3,4):
    rep(f'score>={s}',L.score>=s)
print()
# pares
import itertools
for a,b in itertools.combinations(fam,2):
    m=L[a]&L[b]
    if m.sum()>200: rep(f'{a}+{b}',m)
L.to_pickle(sys.argv[1]+'/L.pkl')
