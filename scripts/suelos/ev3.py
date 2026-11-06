import pandas as pd, numpy as np, sys
A=pd.read_pickle(sys.argv[1]+'/A.pkl')
tr=A.index<'2019-01-01'; te=~tr
def st(m,name):
    out=[name]
    for sel,lab in ((tr,'IS'),(te,'OOS')):
        y=A[m&sel]; b=A[sel].lab.mean()
        out+= [len(y), y.lab.mean()*100 if len(y) else np.nan, (y.lab.mean()/b) if len(y) else np.nan]
    y=A[m]
    out+=[y.lab0.mean()*100,y.r5.mean()*100,y.r10.mean()*100,(y.r10>0).mean()*100,y.nl10.mean()*100,y.mae.mean()*100,y.index.nunique()]
    return out
cols='senal nIS sueloIS liftIS nOOS sueloOOS liftOOS hoy r5 r10 win10 nl10 mae dias'.split()
print('BASE IS %.1f%% OOS %.1f%% r10 %.2f nl10 %.0f%% mae %.1f'%(A[tr].lab.mean()*100,A[te].lab.mean()*100,A.r10.mean()*100,A.nl10.mean()*100,A.mae.mean()*100))
K=A.kl_in|A.kl_rev
S={
'Keltner low->dentro':A.kl_in,'Keltner rev':A.kl_rev,'Keltner (in|rev)':K,
'Bollinger%b<0':A.pb0,'RSI5<20':A.rsi5,'RSI14<30':A.rsi14,'2D-2U':A.d2u,
'2D-2U + Bollinger ayer':A.d2u&A.groupby('tk').pb0.shift().fillna(False).astype(bool),
'Keltner(in|rev)+RSI5<20':K&A.rsi5,
# volumen
'Keltner(in|rev) + relvol hoy>=1.5':K&(A.rv>=1.5),
'Keltner(in|rev) + relvol hoy>=2':K&(A.rv>=2),
'Keltner(in|rev) + relvol max3d>=2':K&(A.rv_max3>=2),
'Keltner(in|rev) + relvol max3d<1.2 (sin climax)':K&(A.rv_max3<1.2),
'Keltner(in|rev) + relvol hoy<0.8 (secado)':K&(A.rv<0.8),
'drop3 + vela alcista + relvol max3>=2':A.drop3&A.rev&(A.rv_max3>=2),
'drop3 + vela alcista (sin vol)':A.drop3&A.rev,
'RSI5<20 + relvol max3>=2':A.rsi5&(A.rv_max3>=2),
'Bollinger + relvol hoy>=2 + vela alcista':A.pb0&(A.rv>=2)&A.rev,
'2D-2U + relvol ayer>=2':A.d2u&(A.rvyest>=2),
'2D-2U + relvol hoy>=1.2':A.d2u&(A.rv>=1.2),
# mercado
'Keltner(in|rev) + VIX spike reciente':K&A.vix_spike_recent,
'Keltner(in|rev) + VIX cae desde pico':K&A.vix_peak_fall,
'Keltner(in|rev) + backwardation reciente':K&A.backw_recent,
'Keltner(in|rev) + VVIX alto':K&A.vvix_hi,
'Keltner(in|rev) + SPY sobrevendido reciente':K&A.spy_osold_recent,
'Keltner(in|rev) + SPY NO sobrevendido':K&~A.spy_osold_recent,
'Keltner(in|rev) + amplitud>=20% bajo Keltner':K&(A.breadth>=.2),
'Keltner(in|rev) + amplitud>=35%':K&(A.breadth>=.35),
'Keltner(in|rev) + amplitud<5%':K&(A.breadth<.05),
'Keltner(in|rev) + SPY dd>5%':K&A.spy_dd5,
'Keltner(in|rev) + SPY dd<=5%':K&~A.spy_dd5,
# combos
'Keltner(in|rev)+relvol max3>=2+VIXspike':K&(A.rv_max3>=2)&A.vix_spike_recent,
'Keltner(in|rev)+relvol max3>=2+amplitud>=20%':K&(A.rv_max3>=2)&(A.breadth>=.2),
'Keltner(in|rev)+amplitud>=20%+VIX cae':K&(A.breadth>=.2)&A.vix_peak_fall,
'Keltner(in|rev)+RSI5<20+relvol max3>=2':K&A.rsi5&(A.rv_max3>=2),
'Keltner(in|rev)+RSI5<20+amplitud>=20%':K&A.rsi5&(A.breadth>=.2),
}
rows=[st(m.values,k) for k,m in S.items() if m.sum()>=40]
R=pd.DataFrame(rows,columns=cols)
pd.set_option('display.width',250);pd.set_option('display.max_colwidth',50)
print(R.round(1).to_string(index=False))
