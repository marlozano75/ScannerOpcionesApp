"""R20: ¿se abre una ventana para cerrar un CDS largo con +20 % del débito antes de la salida a 90 DTE?

Valora cada día un call debit spread (comprada +o %, vendida +o+15 % sobre el precio de entrada) con
Black-Scholes sobre las trayectorias reales de $D/q.pkl (cds_fetch.py). IV = volatilidad realizada de
60 sesiones × 1,1 (suavizada), r = 4 %, sin dividendos. Entradas cada 3 sesiones en tendencia alcista
(cierre > SMA200, SMA50 > SMA200, SMA200 subiendo en 20 sesiones). Se paga el valor teórico + fricción y
se vende a valor − fricción (cierre diario).

    cds_ventana.py $D [fricción=0.03]   tabla por DTE y % OTM, retroceso, años y tickers
    cds_ventana.py $D sens              sensibilidad (DTE 365, +25 %): fricción, ancho y vol fija/móvil
"""
import sys; sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd, numpy as np

def ndtr(x):  # normal acumulada (Abramowitz-Stegun 7.1.26, error < 1,5e-7)
    z = np.abs(x) / np.sqrt(2); t = 1 / (1 + 0.3275911 * z)
    y = 1 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t - 0.284496736)*t + 0.254829592)*t*np.exp(-z*z)
    return 0.5 * (1 + np.sign(x) * y)

R_RATE = 0.04
def bs(S, K, tau, v):
    tau = np.maximum(tau, 1e-6); sv = v * np.sqrt(tau)
    d1 = (np.log(S / K) + (R_RATE + 0.5*v*v) * tau) / sv
    return S * ndtr(d1) - K * np.exp(-R_RATE * tau) * ndtr(d1 - sv)

def simulate(d, dtes, otms, wid=0.15, fr=0.03, fixed_vol=False, step=3, target=0.20, stop=-0.70, exit_dte=90):
    rows = []
    for t in [t for t in d.columns.levels[0] if t != 'SPY']:
        c = d[t]['Close'].dropna()
        if len(c) < 700: continue
        lr = np.log(c).diff(); hv = (lr.rolling(60).std() * np.sqrt(252)).ewm(span=10).mean() * 1.1
        s200 = c.rolling(200).mean(); s50 = c.rolling(50).mean()
        up = ((c > s200) & (s50 > s200) & (s200 > s200.shift(20))).values
        pull = (c < c.rolling(20).max() * 0.93).values
        C = c.values; V = np.clip(hv.values, 0.12, 1.5); idx = c.index; n = len(C)
        for i in range(260, n, step):
            if not up[i] or np.isnan(V[i]): continue
            for dte in dtes:
                h = int(dte * 252 / 365)
                if i + h > n - 1: continue
                steps = np.arange(i, i + h + 1); cal = (steps - i) * 365 / 252
                tau = (dte - cal) / 365; S = C[steps]
                v = np.full(len(steps), V[i]) if fixed_vol else V[steps]
                for o in otms:
                    K1 = C[i] * (1 + o); K2 = C[i] * (1 + o + wid)
                    val = bs(S, K1, tau, v) - bs(S, K2, tau, v)
                    deb = val[0] * (1 + fr); ret = val * (1 - fr) / deb - 1
                    cut = cal <= dte - exit_dte
                    rr = ret[1:][cut[1:]]; cc = cal[1:][cut[1:]]
                    hit = np.where(rr >= target)[0]; st = np.where(rr <= stop)[0]
                    rows.append((t, idx[i].year, dte, o, bool(pull[i]),
                                 cc[hit[0]] if len(hit) else np.nan, cc[st[0]] if len(st) else np.nan,
                                 ret[-1], deb / (K2 - K1)))
    R = pd.DataFrame(rows, columns='t yr dte otm pull fh fs exp_ret debw'.split())
    R['hit'] = R.fh.notna(); R['hit_before_stop'] = R.hit & (R.fs.isna() | (R.fh < R.fs))
    for k in (30, 60, 90, 180): R[f'h{k}'] = R.fh <= k
    R['exp_loss'] = R.exp_ret < 0
    return R

d = pd.read_pickle(sys.argv[1] + '/q.pkl')
pd.set_option('display.width', 250)
if len(sys.argv) > 2 and sys.argv[2] == 'sens':
    for fr, wid, fix in [(0.03, .15, False), (0.03, .15, True), (0.10, .15, True), (0.15, .15, True), (0.10, .40, True), (0.10, .40, False)]:
        R = simulate(d, [365], [0.25], wid, fr, fix, step=6)
        print(f"fric {fr:.0%} ancho {wid:.0%} vol {'fija' if fix else 'móvil'}: deb/ancho {R.debw.median():.2f}  "
              f"toca +20 % {R.hit.mean():.0%}  antes del stop {R.hit_before_stop.mean():.0%}  "
              f"en 60 d {R.h60.mean():.0%}  días (mediana) {R.fh.median():.0f}")
    sys.exit()
fr = float(sys.argv[2]) if len(sys.argv) > 2 else 0.03
R = simulate(d, [250, 365, 500], [0.10, 0.25, 0.40], fr=fr)
g = R.groupby(['dte', 'otm']).agg(n=('hit', 'size'), deb_ancho=('debw', 'median'), toca20=('hit', 'mean'),
    antes_stop=('hit_before_stop', 'mean'), en30d=('h30', 'mean'), en60d=('h60', 'mean'), en90d=('h90', 'mean'),
    en180d=('h180', 'mean'), dias_med=('fh', 'median'), pierde_al_vencer=('exp_loss', 'mean'))
pc = [c for c in g.columns if c not in ('n', 'deb_ancho', 'dias_med')]; g[pc] *= 100
print(f'TENDENCIA ALCISTA, ventana hasta 90 DTE, fricción {fr:.0%} por lado, ancho 15 % del precio'); print(g.round(1).to_string())
x = R[R.dte == 365]
print('\nCon retroceso >= 7 % frente a sin él (DTE 365)')
print((x.groupby(['otm', 'pull'])[['hit', 'hit_before_stop', 'h30']].mean() * 100).round(0).to_string())
print('\nToca +20 % por año, DTE 365, +25 %'); print((x[x.otm == 0.25].groupby('yr')[['hit', 'h60']].mean() * 100).round(0).T.to_string())
print('\nPor ticker, DTE 365, +25 %'); print((x[x.otm == 0.25].groupby('t').hit.mean().sort_values() * 100).round(0).to_frame().T.to_string())
