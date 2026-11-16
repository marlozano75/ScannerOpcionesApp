"""Ronda R19 (2026-11-16): canal de regresión de 10 años (2500 sesiones) con bandas de −3σ / −4σ. Sin tocar data/app.db
(solo la lee para la capitalización de hoy).

Estrategia de un vídeo de YouTube con el indicador «Standard Deviation Channel» de TradingView: regresión LINEAL por
mínimos cuadrados de los cierres de las últimas 2500 sesiones; σ = raíz de Σ(cierre − recta)² / (n − 1); bandas = recta ± k·σ.
Compra al cruzar −3σ (las 10 mayores por capitalización) o −4σ (el resto) en acciones de más de 50.000 M$, con pendiente
positiva y la banda por encima de 0 en todo el canal; vende al tocar la recta; sin stop.

Uso (desde la raíz del repo): .venv/Scripts/python scripts/suelos/canal10.py CARPETA
   CARPETA/yf.pkl (27 tickers) y CARPETA/yf_val_ok.pkl (2.179) de fetch_yf.py y validacion_fetch.py/validacion_filtro.py.

Punto en el tiempo: el canal de cada día se ajusta con los 2500 cierres hasta ese día (lo que el indicador pinta en
«replay»). Capitalización del día ≈ capitalización de hoy (ticker_quality.market_cap) × cierre del día / último cierre
(supone acciones en circulación constantes). Solo acciones (las que tienen capitalización; sin ETF). Resultados con el
cierre ajustado por dividendos. Controles: media de todas las acciones elegibles (≥ 50.000 M$ ese día) del mismo día.
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1])
N = 2500
CAP = 50e9
R = pd.Series


def fwd(a, k):
    return np.r_[a[k:], np.full(k, np.nan)]


def roll_sum(a, n):
    cs = np.r_[0.0, np.cumsum(a)]
    out = np.full(len(a), np.nan)
    out[n - 1:] = cs[n:] - cs[:-n]
    return out


def channel(y):
    """Recta de regresión de los últimos N valores (por día) y σ de sus residuos, como el indicador de Pine."""
    n = len(y)
    x = np.arange(n, dtype=float)
    sy, sxy, syy = roll_sum(y, N), roll_sum(x * y, N), roll_sum(y * y, N)
    xm = x - (N - 1) / 2                      # media de x en la ventana que acaba en t
    sxx = N * (N * N - 1) / 12
    ym = sy / N
    b = (sxy - N * xm * ym) / sxx
    end = ym + b * (N - 1) / 2
    start = ym - b * (N - 1) / 2
    ssr = np.maximum(syy - N * ym * ym - b * b * sxx, 0)
    sd = np.sqrt(ssr / (N - 1))
    return end, start, b, sd


def per_ticker(t, x, cap_now):
    c = x["Close"].to_numpy(float)
    ac = x["Adj Close"].to_numpy(float)
    h, l = x["High"].to_numpy(float), x["Low"].to_numpy(float)
    if len(c) < N + 50:
        return None
    end, start, b, sd = channel(c)
    lend, lstart, lb, lsd = channel(np.log(c))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    atr = R(np.nan_to_num(tr, nan=h[0] - l[0])).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    F = pd.DataFrame(index=x.index)
    F["c"], F["ac"] = c, ac
    F["line"], F["lline"] = end, lend
    with np.errstate(divide="ignore", invalid="ignore"):
        F["z"] = np.where(sd > 0, (c - end) / sd, np.nan)
        F["zl"] = np.where(lsd > 0, (np.log(c) - lend) / lsd, np.nan)
    F["slope"], F["lslope"] = b, lb
    # «la banda por encima de 0»: en el extremo actual del canal. Medida en todo el canal (su inicio), excluía la compra
    # de MSFT del propio vídeo (2026-03-27): con 10 años de subida, el inicio de la banda −3σ queda negativo.
    F["bmin3"] = end - 3 * sd
    F["bmin4"] = end - 4 * sd
    F["bini4"] = np.minimum(start, end) - 4 * sd
    F["sd"] = sd
    F["cap"] = cap_now * c / c[-1]
    for H in (63, 126, 252):
        F[f"r{H}"] = (fwd(ac, H) / ac - 1) * 100
    mn = R(ac[::-1]).rolling(252, min_periods=252).min().to_numpy()[::-1]
    F["mdd252"] = (np.r_[mn[1:], np.nan] / ac - 1) * 100
    lo21 = R(c[::-1]).rolling(21, min_periods=21).min().to_numpy()[::-1]
    F["h21"] = np.where(np.isnan(fwd(c, 21)), np.nan, np.r_[lo21[1:], np.nan] > c - 2 * atr)      # put a 2 ATR, 21 ses.
    lo35 = R(ac[::-1]).rolling(35, min_periods=35).min().to_numpy()[::-1]
    F["h35"] = np.where(np.isnan(fwd(c, 35)), np.nan, np.r_[lo35[1:], np.nan] > 0.9 * ac)        # put un 10 % OTM, 35 ses.
    F = F.iloc[N - 1:].copy()
    F["tk"] = t
    return F


def load():
    con = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
    caps = dict(con.execute("select ticker, market_cap from ticker_quality where market_cap is not null"))
    out = []
    for f in ("yf.pkl", "yf_val_ok.pkl"):
        Y = pd.read_pickle(D / f)
        for t in Y.columns.get_level_values(0).unique():
            if t.startswith("^") or t not in caps:
                continue
            x = Y[t].dropna(subset=["Close", "Adj Close"])
            x = x[(x.High >= x.Low) & (x.Close > 0)]
            F = per_ticker(t, x, caps[t])
            if F is not None:
                out.append(F)
        del Y
    G = pd.concat(out)
    G.index.name = "day"
    return G


G = load()
G = G.reset_index().sort_values(["tk", "day"]).reset_index(drop=True)      # serie completa y consecutiva por ticker
G = G[np.isfinite(G.z) & np.isfinite(G.zl)].reset_index(drop=True)
G["rank"] = G.groupby("day")["cap"].rank(ascending=False)
G["elig"] = G.cap >= CAP
G["d1"] = G.groupby("tk").ac.pct_change()
IDX = G.groupby("tk").indices
OUT = ["r63", "r126", "r252", "mdd252", "h21", "h35"]
E = G[G.elig]
BASE = E.groupby("day")[OUT].mean()                      # control: media de las elegibles del mismo día
EW = (1 + E.groupby("day").d1.mean().fillna(0)).cumprod()   # igual peso de las elegibles (misma supervivencia)
print("acciones con 10 años", G.tk.nunique(), "filas", len(G), "| elegibles ≥50.000 M$:", E.tk.nunique(), "tickers", len(E),
      "filas", E.day.min().date(), E.day.max().date())

# ---------- comprobación con las fechas del vídeo ----------
print("\n== Comprobación con el vídeo ==")
V = G.set_index(["tk", "day"])
for t, a, b in (("MSFT", "2026-03-20", "2026-04-02"), ("MSFT", "2026-07-27", "2026-08-07"), ("META", "2022-06-01", "2022-07-08")):
    x = V.loc[t].loc[a:b, ["c", "line", "z", "zl", "rank"]]
    print(t, a, b)
    print(x.round(2).to_string())
for t in ("MSFT", "META", "ADBE", "MCD"):
    x = V.loc[t]
    print(f"{t} último {x.index[-1].date()} cierre {x.c.iloc[-1]:.2f} recta {x.line.iloc[-1]:.2f} z {x.z.iloc[-1]:+.2f} zlog {x.zl.iloc[-1]:+.2f} "
          f"−3σ {x.line.iloc[-1] - 3 * x.sd.iloc[-1]:.2f} −4σ {x.line.iloc[-1] - 4 * x.sd.iloc[-1]:.2f} | mín z 2026 {x.loc['2026'].z.min():+.2f} "
          f"el {x.loc['2026'].z.idxmin().date()}")
mz = V.loc["META"].loc["2022"]
print("META 2022: mín z", round(mz.z.min(), 2), mz.z.idxmin().date(), "| primer día z ≤ −4:", mz.index[mz.z <= -4].min())
del V


def cross_rows(zc, k, cond, rearm=63):
    """Filas (de G) en que z cruza por debajo de −k respecto a la sesión anterior real, sin otro cruce en `rearm`
    sesiones; las condiciones (capitalización, pendiente, banda) solo se exigen el día del cruce."""
    z = G[zc].to_numpy()
    out = []
    for ix in IDX.values():
        zz, kk, cc = z[ix], k[ix], cond[ix]
        cr = (zz <= -kk) & (np.r_[np.inf, zz[:-1]] > -kk)
        cr[0] = False
        last = -10**9
        for i in np.flatnonzero(cr):
            if i - last > rearm and cc[i]:
                out.append(ix[i])
            last = i
    return np.array(out, dtype=int)


def ev(nm, rows):
    z = G.loc[rows]
    if len(z) < 5:
        print(f"{nm:52s} n={len(z)}")
        return
    d = pd.DataFrame(z[OUT].to_numpy() - BASE.loc[z.day, OUT].to_numpy(), columns=OUT, index=z.index)
    is_ = (z.day < "2020-01-01").to_numpy()
    s = f"{nm:52s} n={len(z):5d} días={z.day.nunique():4d} "
    for k in ("r63", "r126", "r252"):
        s += f"{k} {z[k].mean():+5.1f}({d[k].mean():+5.1f}) "
    s += f"r252 med {z.r252.median():+5.1f} %pos252 {(z.r252 > 0).mean()*100:4.1f} mdd252 {z.mdd252.mean():+5.1f}({d.mdd252.mean():+4.1f}) "
    s += f"h21 {z.h21.mean()*100:4.1f}({d.h21.mean()*100:+5.1f}) h35 {z.h35.mean()*100:4.1f}({d.h35.mean()*100:+5.1f}) "
    s += f"| r252 dif <2020 {d.r252[is_].mean():+5.1f} (n {is_.sum()}) ≥2020 {d.r252[~is_].mean():+5.1f} (n {(~is_).sum()})"
    m = d.r252.groupby(z.day.dt.to_period("M").to_numpy()).mean()          # un voto por mes
    s += f" | por mes r252 dif {m.mean():+5.1f} (meses {len(m)}, >0 {(m > 0).mean()*100:3.0f} %)"
    print(s)


n = len(G)
el = G.elig.to_numpy()
up = (G.slope > 0).to_numpy()
lup = (G.lslope > 0).to_numpy()
b3, b4 = (G.bmin3 > 0).to_numpy(), (G.bmin4 > 0).to_numpy()
top10 = (G["rank"] <= 10).to_numpy()
K = {k: np.full(n, float(k)) for k in (2, 3, 4)}
KV = np.where(top10, 3.0, 4.0)
CV = el & up & np.where(top10, b3, b4)                   # regla del vídeo

print("\n== Estudio de eventos, elegibles ≥ 50.000 M$; entre paréntesis, diferencia frente al mismo día ==")
print(f"{'base: todos los días elegibles':52s} r63 {E.r63.mean():+5.1f} r126 {E.r126.mean():+5.1f} r252 {E.r252.mean():+5.1f} "
      f"%pos252 {(E.r252 > 0).mean()*100:4.1f} mdd252 {E.mdd252.mean():+5.1f} h21 {E.h21.mean()*100:4.1f} h35 {E.h35.mean()*100:4.1f}")
for zc, nmz in (("z", "lineal"), ("zl", "log")):
    for k in (2, 3, 4):
        ev(f"{nmz} cruza −{k}σ", cross_rows(zc, K[k], el))
for k, bb in ((3, b3), (4, b4)):
    ev(f"lineal cruza −{k}σ + pendiente>0 + banda>0", cross_rows("z", K[k], el & up & bb))
    ev(f"log cruza −{k}σ + pendiente>0", cross_rows("zl", K[k], el & lup))
VID = cross_rows("z", KV, CV)
ev("REGLA DEL VÍDEO (top10 −3σ, resto −4σ, filtros)", VID)
ev("  solo top 10 a −3σ", cross_rows("z", K[3], CV & top10))
ev("  resto a −4σ", cross_rows("z", K[4], CV & ~top10))
ev("misma regla con canal log", cross_rows("zl", KV, el & lup))
G["dd"] = G.c / G.groupby("tk").c.transform(lambda s: s.rolling(252, min_periods=1).max()) - 1
G["ddz"] = G.dd * 100 / 25                                # control sin canal: cruza −1 ⇔ cae un 25 % desde el máx. de 252
ev("control: cae −25 % desde máx. 252 sesiones", cross_rows("ddz", np.full(n, 1.0), el))
zz = G.loc[VID]
print("cruces del vídeo por año:", zz.day.dt.year.value_counts().sort_index().to_dict(),
      "| caída desde máx. 252 al cruzar (mediana):", f"{zz.dd.median()*100:.1f} %")
print(zz[["day", "tk", "c", "z", "rank", "dd", "r252"]].sort_values("day").round({"c": 2, "z": 2, "dd": 2, "r252": 1}).to_string(index=False))


def trades(zc, k, cond, log=False):
    """Una posición por ticker: entra al cierre del cruce y sale al primer cierre ≥ recta de ese día (sin stop),
    sobre la serie completa. `exc` = rentabilidad menos la del igual peso de las elegibles en las mismas fechas."""
    z = G[zc].to_numpy(); c = G.c.to_numpy(); ac = G.ac.to_numpy()
    ln = np.exp(G.lline.to_numpy()) if log else G.line.to_numpy()
    day = G.day.to_numpy()
    rows = []
    for t, ix in IDX.items():
        zz, kk, cc = z[ix], k[ix], cond[ix]
        i, m = 1, len(ix)
        while i < m:
            if cc[i] and zz[i] <= -kk[i] and zz[i - 1] > -kk[i]:
                j = i + 1
                while j < m and c[ix[j]] < ln[ix[j]]:
                    j += 1
                closed = j < m
                j = min(j, m - 1)
                seg = ac[ix[i]:ix[j] + 1]
                ret = (ac[ix[j]] / ac[ix[i]] - 1) * 100
                ewr = (EW.asof(day[ix[j]]) / EW.asof(day[ix[i]]) - 1) * 100
                rows.append(dict(tk=t, entra=day[ix[i]], sale=day[ix[j]], cerrada=closed, dias=j - i, ret=ret,
                                 exc=ret - ewr, dd=(seg.min() / ac[ix[i]] - 1) * 100))
                i = j + 1
            else:
                i += 1
    return pd.DataFrame(rows)


def show(nm, T):
    cl = T[T.cerrada]
    ann = ((1 + cl.ret / 100) ** (252 / cl.dias.clip(lower=1)) - 1) * 100
    print(f"{nm:40s} n={len(T):4d} cerradas {len(cl)/len(T)*100:5.1f} % | sesiones med {cl.dias.median():4.0f} p90 {cl.dias.quantile(.9):4.0f} "
          f"| ret med {cl.ret.median():+5.1f} % | exceso vs igual peso: med {T.exc.median():+5.1f} media {T.exc.mean():+5.1f} >0 {(T.exc > 0).mean()*100:4.1f} % "
          f"| anual. med {ann.median():+6.1f} % | DD med {T.dd.median():+5.1f} % p10 {T.dd.quantile(.1):+5.1f} % ≤−30 % {(T.dd <= -30).mean()*100:4.1f} % "
          f"| abiertas {(~T.cerrada).sum()} ret med {T[~T.cerrada].ret.median() if (~T.cerrada).any() else float('nan'):+5.1f} %")


print("\n== Operaciones (salida al primer cierre ≥ recta, sin stop) ==")
TV = trades("z", KV, CV)
show("regla del vídeo (lineal)", TV)
show("misma regla, canal log", trades("zl", KV, el & lup, log=True))
T3 = trades("z", K[3], el & up)
show("lineal, todas a −3σ, pendiente > 0", T3)
show("lineal, todas a −4σ, pendiente > 0", trades("z", K[4], el & up))
print(TV.sort_values("entra").round({"ret": 1, "exc": 1, "dd": 1}).to_string(index=False))

# ---------- cartera como la del vídeo ----------
PX = G.pivot(index="day", columns="tk", values="ac").ffill()
COL = {t: i for i, t in enumerate(PX.columns)}


def portfolio(T, start, w=0.10, maxpos=10):
    """w del capital por posición (máx. maxpos), efectivo al 0 %; posiciones valoradas con su último cierre ajustado."""
    T = T[T.entra >= start]
    px = PX[PX.index >= start]
    P = px.to_numpy()
    days = px.index
    byday = {d: g for d, g in T.sort_values("entra").groupby("entra")}
    cash, pos, eq, inv, used = 1.0, {}, [], [], 0
    for r, d in enumerate(days):
        row = P[r]
        for t in [t for t, p in pos.items() if p[1] is not None and p[1] <= d]:
            cash += pos[t][0] * row[COL[t]]
            del pos[t]
        val = cash + sum(p[0] * row[COL[t]] for t, p in pos.items())
        if d in byday:
            for _, tr in byday[d].iterrows():
                pr = row[COL[tr.tk]]
                if len(pos) < maxpos and tr.tk not in pos and cash >= w * val * 0.999 and np.isfinite(pr):
                    pos[tr.tk] = (w * val / pr, tr.sale if tr.cerrada else None)
                    cash -= w * val
                    used += 1
        v = cash + sum(p[0] * row[COL[t]] for t, p in pos.items())
        eq.append(v)
        inv.append(1 - cash / v)
    return R(eq, index=days), used, np.mean(inv) * 100


def stats(s):
    s = s / s.iloc[0]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return s.iloc[-1], (s.iloc[-1] ** (1 / yrs) - 1) * 100, ((s / s.cummax()) - 1).min() * 100


Y27 = pd.read_pickle(D / "yf.pkl")
print("\n== Cartera (efectivo sin rentabilidad) ==")
for nm, T, w, mp in (("regla del vídeo, 10 %, máx. 10", TV, .10, 10), ("regla del vídeo, 5 %, máx. 20", TV, .05, 20),
                     ("todas a −3σ, pendiente > 0", T3, .10, 10)):
    for start in ("2014-01-01", "2016-01-01"):
        s, used, inv = portfolio(T, start, w, mp)
        m, cg, md = stats(s)
        ye = s.resample("YE").last()
        yr = (ye / ye.shift(1).fillna(s.iloc[0]) - 1) * 100
        print(f"{nm:32s} desde {start}: ×{m:.2f} CAGR {cg:+.1f} % maxDD {md:.1f} % invertido medio {inv:.0f} % operaciones {used}")
        print("   por año:", {k.year: round(v, 1) for k, v in yr.items()})
for start in ("2014-01-01", "2016-01-01"):
    for b in ("SPY", "QQQ"):
        a = Y27[b]["Adj Close"].dropna()
        m, cg, md = stats(a[a.index >= start])
        print(f"   {b} desde {start}: ×{m:.2f} CAGR {cg:+.1f} % maxDD {md:.1f} %")
    m, cg, md = stats(EW[EW.index >= start])
    print(f"   igual peso de las elegibles desde {start} (misma supervivencia): ×{m:.2f} CAGR {cg:+.1f} % maxDD {md:.1f} %")


