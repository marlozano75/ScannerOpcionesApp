"""Ronda R18 (2026-11-15): squeeze de Bollinger, ¿anticipa la dirección del movimiento? Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/squeeze.py CARPETA          # CARPETA/yf.pkl (27 tickers)
     .venv/Scripts/python scripts/suelos/squeeze.py CARPETA val      # CARPETA/yf_val_ok.pkl (2.179 tickers)

Todos los días (no solo tras un impulso). Squeeze = ancho de Bollinger (20, 2) en su 20 % inferior de 120 sesiones, o TTM
(Bollinger dentro de Keltner EMA20 ± 1,5 ATR20); «dispara» = salía de squeeze en las 5 sesiones previas y cierra fuera de la
banda (o sale del TTM con cierre sobre/bajo la EMA20). Resultados al cierre de t: sube/baja primero ±1,5 ATR (10 y 20 sesiones),
toca +1,5 / −1,5 ATR alguna vez, cierra arriba a 20 d, MFE/MAE a 20 sesiones en ATR. Diferencias frente a la media de todos
los tickers del mismo día, mitades 2004-2018 / 2019-2026.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1])
which = sys.argv[2] if len(sys.argv) > 2 else "27"
R = pd.Series


def bk(a, k):
    return np.r_[np.full(k, np.nan), a[:-k]] if k else a


def fwd(a, k):
    return np.r_[a[k:], np.full(k, np.nan)]


def ld(Y):
    for t in Y.columns.get_level_values(0).unique():
        if t.startswith("^"):
            continue
        x = Y[t].dropna(subset=["Close"])
        x = x[(x.High >= x.Low) & (x.Close > 0)]
        if len(x) >= 400:
            yield t, x


def per_ticker(t, x):
    o, h, l, c = (x[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    pc = bk(c, 1)
    tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    a = R(np.nan_to_num(tr, nan=h[0] - l[0])).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    sma = R(c).rolling(20).mean().to_numpy(); sd = R(c).rolling(20).std().to_numpy()
    ub, lb = sma + 2 * sd, sma - 2 * sd
    bw = 4 * sd / sma
    bwq = R(bw).rolling(120).rank(pct=True).to_numpy()
    ema20 = R(c).ewm(span=20, adjust=False).mean().to_numpy()
    a20 = R(np.nan_to_num(tr, nan=0)).rolling(20).mean().to_numpy()
    ttm = (ub < ema20 + 1.5 * a20) & (lb > ema20 - 1.5 * a20)          # Bollinger dentro de Keltner (TTM)
    s50 = R(c).rolling(50).mean().to_numpy(); s200 = R(c).rolling(200).mean().to_numpy()
    sq = bwq <= 0.2
    sq_prev = R(sq.astype(float)).shift(1).rolling(5).max().to_numpy() > 0
    ttm_prev = R(ttm.astype(float)).shift(1).rolling(5).max().to_numpy() > 0
    # resultados: primer toque de +k ATR / -k ATR en 10 y 20 sesiones, MFE/MAE 20 en ATR
    res = {}
    for H in (10, 20):
        fu = np.full(n, 99); fd = np.full(n, 99)
        for k in range(H, 0, -1):
            fu = np.where(fwd(h, k) >= c + 1.5 * a, k, fu)
            fd = np.where(fwd(l, k) <= c - 1.5 * a, k, fd)
        ok = np.arange(n) + H < n
        res[f"up{H}"] = np.where(ok, fu < fd, np.nan)          # sube primero
        res[f"dn{H}"] = np.where(ok, fd < fu, np.nan)          # baja primero
        res[f"tu{H}"] = np.where(ok, fu < 99, np.nan)          # toca +1,5 ATR alguna vez
        res[f"td{H}"] = np.where(ok, fd < 99, np.nan)
        if H == 20:
            res["dias_up"] = np.where(ok & (fu < 99), fu, np.nan)
    mx = R(h[::-1]).rolling(20, min_periods=20).max().to_numpy()[::-1]
    mn = R(l[::-1]).rolling(20, min_periods=20).min().to_numpy()[::-1]
    aok = np.where(a > 0.002 * c, a, np.nan)              # sin ATR casi nulo (precios planos) que dispare MFE/MAE
    res["mfe20"] = (np.r_[mx[1:], np.nan] - c) / aok
    res["mae20"] = (c - np.r_[mn[1:], np.nan]) / aok
    res["a20"] = np.where(np.arange(n) + 20 < n, fwd(c, 20) > c, np.nan)
    res["r20"] = (fwd(c, 20) / c - 1) * 100
    F = pd.DataFrame(res, index=x.index)
    up_t = (s50 > s200) & (c > s50)
    F["sq"] = sq
    F["ttm"] = ttm
    F["fire_up"] = sq_prev & (c > ub)                 # salía de squeeze y cierra sobre la banda superior
    F["fire_dn"] = sq_prev & (c < lb)
    F["ttm_fire_up"] = ttm_prev & ~ttm & (c > ema20) & (c > pc)
    F["ttm_fire_dn"] = ttm_prev & ~ttm & (c < ema20) & (c < pc)
    F["tend"] = up_t
    F["bwq"] = bwq
    F = F.iloc[260:]
    F["tk"] = t
    return F


Y = pd.read_pickle(D / ("yf.pkl" if which == "27" else "yf_val_ok.pkl"))
G = pd.concat(per_ticker(t, x) for t, x in ld(Y))
G.index.name = "day"
M = ["up10", "dn10", "tu10", "td10", "up20", "dn20", "tu20", "td20", "a20"]
dm = G.groupby(level=0)[M + ["mfe20", "mae20", "r20"]].transform("mean")
IS = G.index < "2019-01-01"


def row(nm, m):
    m = np.asarray(m, bool)
    z = G[m]
    if len(z) < 100:
        print(f"{nm:42s} n={len(z)}"); return
    s = f"{nm:42s} n={len(z):8d} "
    for k in M:
        s += f"{k} {z[k].mean()*100:5.1f}({(z[k]-dm[k][m]).mean()*100:+4.1f}) "
    s += f"mfe {z.mfe20.mean():4.2f}({(z.mfe20-dm.mfe20[m]).mean():+.2f}) mae {z.mae20.mean():4.2f}({(z.mae20-dm.mae20[m]).mean():+.2f}) "
    s += f"r20 {z.r20.mean():+5.2f} días_hasta_+1,5 {z.dias_up.median():.0f} "
    s += f"| up10 dif IS {(z.up10-dm.up10[m])[IS[m]].mean()*100:+4.1f} OOS {(z.up10-dm.up10[m])[~IS[m]].mean()*100:+4.1f}"
    s += f" a20 dif IS {(z.a20-dm.a20[m])[IS[m]].mean()*100:+4.1f} OOS {(z.a20-dm.a20[m])[~IS[m]].mean()*100:+4.1f}"
    print(s)


print("muestra", which, "tickers", G.tk.nunique(), "filas", len(G))
row("todos", np.ones(len(G), bool))
for lo, hi in ((0, .1), (.1, .2), (.2, .5), (.5, .8), (.8, 1.01)):
    row(f"percentil ancho BB {lo}-{hi}", (G.bwq > lo) & (G.bwq <= hi) if lo else G.bwq <= hi)
row("TTM squeeze activo", G.ttm)
row("squeeze dispara al alza (c > banda sup.)", G.fire_up)
row("squeeze dispara a la baja (c < banda inf.)", G.fire_dn)
row("TTM dispara al alza", G.ttm_fire_up)
row("TTM dispara a la baja", G.ttm_fire_dn)
row("squeeze activo + tendencia alcista", G.sq & G.tend)
row("squeeze dispara al alza + tendencia", G.fire_up & G.tend)
row("TTM dispara al alza + tendencia", G.ttm_fire_up & G.tend)
