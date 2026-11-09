"""Ronda R12 (2026-11-09): reglas de entrada basadas en medias móviles como soporte. Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/medias_reglas.py CARPETA        # necesita CARPETA/yf.pkl de fetch_yf.py
     .venv/Scripts/python scripts/suelos/medias_reglas.py CARPETA db    # muestra de validación: velas de data/app.db (solo lectura)

Setups (señal al CIERRE del día t; filtros de régimen y tendencia con datos hasta t-1; entrada al cierre de t):
  S10  Schwartz EMA10: tendencia (5 cierres previos sobre EMA10 y EMA10 subiendo), el mínimo toca EMA10 + 0,25 ATR y cierra sobre ella.
       Variantes: primer toque (ninguno en 10 sesiones), con SMA50 > SMA200, con mercado alcista (SPY).
  S8   8 EMA (bigwavechartist): estructura = EMA8 de máximos/cierres/mínimos. Filtros: cierre > SMA50 y a <= 20 % del máximo de 52 semanas.
       Pullback con volumen relativo medio de 3 días < 1 y rango medio de 3 días < rango medio de 14; entrada: el cierre vuelve sobre
       EMA8 (venía bajo o tocando), volumen mayor que el día anterior y cierre <= EMA8(máx) + 0,5 ATR. Invalidación = EMA8(mín).
  D5   DTR «primer toque de la SMA5 tras ruptura»: ruptura = cierre sobre el máximo de 20 (o 55) sesiones previas; señal = primer día
       posterior (2..10 sesiones) cuyo mínimo toca la SMA5 y cierra sobre ella. Se desglosa por días desde la ruptura.
  Colocación: lo mismo con nivel = EMA10 - 1 ATR (el «mismo nivel más abajo»), para ver si la media aporta algo frente a un nivel cualquiera.

Métricas (ver docs/deteccion-suelos.md): a10 = cierra sobre el de entrada a 10 sesiones; win = sube +1,5 ATR antes de bajar -1,5 ATR en 10 (el
día en que ocurren las dos cuenta como no ganada); h21/h35 = ningún cierre bajo entrada - 2 ATR en 21/35 sesiones (venta de put a 2 ATR);
lvl21 = ningún cierre bajo el nivel (media) - 0,5 ATR en 21 sesiones; mae21 = peor mínimo a 21 sesiones frente a la entrada (%).
Control principal = media de TODOS los tickers del mismo día; IC95 % por bootstrap de días.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

H = 35


def load_yf(path):
    Y = pd.read_pickle(path)
    out = {}
    for t in Y.columns.get_level_values(0).unique():
        if t.startswith("^"):
            continue
        x = Y[t].dropna(subset=["Close"])
        x = x[(x.High >= x.Low)]
        out[t] = pd.DataFrame({"open": x.Open, "high": x.High, "low": x.Low, "close": x.Close, "volume": x.Volume.fillna(0)})
    return out


def load_db():
    con = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT ticker, day, open, high, low, close, volume FROM daily_bars WHERE provisional = 0 AND high IS NOT NULL "
                           "AND low IS NOT NULL ORDER BY ticker, day", con)
    out = {}
    for t, g in df.groupby("ticker"):
        g = g.reset_index(drop=True)
        j = np.where(np.abs(g.close.pct_change().to_numpy()) > 0.4)[0]
        if len(j):
            g = g.iloc[j[-1]:]
        if len(g) >= 330:
            g = g.copy()
            g.index = pd.to_datetime(g.day)
            out[t] = g[["open", "high", "low", "close", "volume"]].astype(float)
    return out


def atr14(h, l, c):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()


def sh(a, k, fill=np.nan):
    """a[t-k]."""
    o = np.full(len(a), fill, dtype=float)
    o[k:] = a[:-k] if k else a
    return o


def feats(t, g, mkt):
    o, h, l, c, v = (g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    a = atr14(h, l, c)
    S = lambda x: pd.Series(x)
    e10 = S(c).ewm(span=10, adjust=False).mean().to_numpy()
    e8 = S(c).ewm(span=8, adjust=False).mean().to_numpy()
    e8h = S(h).ewm(span=8, adjust=False).mean().to_numpy()
    e8l = S(l).ewm(span=8, adjust=False).mean().to_numpy()
    s5 = S(c).rolling(5).mean().to_numpy()
    s50 = S(c).rolling(50).mean().to_numpy()
    s200 = S(c).rolling(200).mean().to_numpy()
    hi252 = S(h).rolling(252).max().to_numpy()
    rv = v / S(v).rolling(20).mean().shift(1).to_numpy()
    rv3 = S(rv).rolling(3).mean().to_numpy()
    rng = h - l
    rg3, rg14 = S(rng).rolling(3).mean().to_numpy(), S(rng).rolling(14).mean().to_numpy()
    F = pd.DataFrame({"c": c, "a": a}, index=g.index)
    # --- resultados (entrada al cierre de t)
    up = np.zeros(n, bool); dn = np.zeros(n, bool); first_up = np.full(n, 99); first_dn = np.full(n, 99)
    for k in range(10, 0, -1):
        hk = np.r_[h[k:], np.full(k, np.nan)]; lk = np.r_[l[k:], np.full(k, np.nan)]
        first_up = np.where(hk >= c + 1.5 * a, k, first_up); first_dn = np.where(lk <= c - 1.5 * a, k, first_dn)
    ok10 = np.arange(n) + 10 < n
    F["a10"] = np.where(ok10, np.r_[c[10:], np.full(10, np.nan)] > c, np.nan)
    F["win"] = np.where(ok10, (first_up < first_dn).astype(float), np.nan)

    mc21 = np.r_[S(c[::-1]).rolling(21, min_periods=21).min().to_numpy()[::-1][1:], np.nan]
    mc35 = np.r_[S(c[::-1]).rolling(35, min_periods=35).min().to_numpy()[::-1][1:], np.nan]
    ml21 = np.r_[S(l[::-1]).rolling(21, min_periods=21).min().to_numpy()[::-1][1:], np.nan]
    F["h21"] = np.where(np.isnan(mc21), np.nan, (mc21 >= c - 2 * a).astype(float))
    F["h35"] = np.where(np.isnan(mc35), np.nan, (mc35 >= c - 2 * a).astype(float))
    F["mae21"] = (ml21 / c - 1) * 100
    F["mc21"] = mc21
    # --- tendencia / régimen con datos de t-1
    c1, e101 = sh(c, 1), sh(e10, 1)
    above5 = np.all([sh(c, k) > sh(e10, k) for k in range(1, 6)], axis=0)
    rising = sh(e10, 1) > sh(e10, 6)
    touch10 = (l <= e10 + 0.25 * a) & (c >= e10)
    prior_touch = np.any([sh(touch10.astype(float), k, 0) > 0 for k in range(1, 11)], axis=0)
    F["tr10"] = above5 & rising                              # tendencia previa de S10
    F["s10"] = F.tr10 & touch10
    F["s10_first"] = F.s10 & ~prior_touch
    F["tr_ma"] = sh(s50, 1) > sh(s200, 1)
    F["above200"] = sh(c, 1) > sh(s200, 1)
    F["s10_ma"] = F.s10 & F.tr_ma & F.above200
    F["nt10"] = F.tr10 & ~touch10                            # control de tendencia sin toque
    F["lvl_s10"] = e10
    # colocación: nivel = EMA10 - 1 ATR
    pl = e10 - a
    ptouch = (l <= pl + 0.25 * a) & (c >= pl)
    F["plc"] = F.tr10 & ptouch & ~touch10
    # --- S8
    pre = (sh(c, 1) > sh(s50, 1)) & (sh(c, 1) >= 0.8 * sh(hi252, 1)) & (c > 10)
    pull = (rv3 < 1) & (rg3 < rg14)
    cross = (c > e8) & (sh(c, 1) <= sh(e8, 1) * 1.003)
    F["s8"] = pre & sh(pull.astype(float), 1, 0).astype(bool) & cross & (v > sh(v, 1)) & (c <= e8h + 0.5 * a)
    F["s8_all"] = pre & cross & (c <= e8h + 0.5 * a)           # sin volumen/compresión
    F["s8_ctl"] = pre & (c > e8) & (sh(c, 1) > sh(e8, 1))      # control: ya sobre EMA8 sin pullback
    F["lvl_s8"] = e8l
    # --- D5
    for w in (20, 55):
        prior_hi = sh(S(h).rolling(w).max().to_numpy(), 1)
        brk = c > prior_hi
        since = np.full(n, 999)
        last = -999
        for i in range(n):
            if brk[i]:
                last = i
            since[i] = i - last
        t5 = (l <= s5) & (c >= s5)
        cand = t5 & (since >= 2) & (since <= 10)
        # primer toque tras la ruptura: ninguno de los días entre la ruptura y t-1 (>=1) toca la SMA5
        first = np.zeros(n, bool)
        for i in np.where(cand)[0]:
            b = i - since[i]
            first[i] = not np.any(t5[b + 1:i])
        F[f"d5_{w}"] = first
        F[f"d5_{w}_since"] = np.where(first, since, 0)
        F[f"d5_{w}_any"] = cand & ~first
    F["lvl_d5"] = s5
    # mercado (SPY, t-1)
    F["mkt"] = mkt.reindex(F.index).shift(1).fillna(False).to_numpy(bool)
    # nivel -> lvl21 (ningún cierre bajo nivel - 0,5 ATR en 21 sesiones)
    for nm, lv in (("s10", e10), ("s8", e8l), ("d5", s5)):
        F[f"lv21_{nm}"] = np.where(np.isnan(mc21), np.nan, (mc21 >= lv - 0.5 * a).astype(float))
    F["warm"] = np.arange(n) >= 260
    F["tk"] = t
    return F[F.warm & (F.c > 0)]


def market(data):
    s = data["SPY"].close
    ok = (s > s.rolling(5).mean()) & (s.rolling(5).mean() > s.rolling(5).mean().shift(3)) & (s.rolling(10).mean() > s.rolling(20).mean())
    return ok


def boot_day(diff, days, rng, B=2000):
    d = pd.DataFrame({"d": diff, "day": days}).dropna().groupby("day").d.agg(["sum", "size"])
    s, m = d["sum"].to_numpy(), d["size"].to_numpy()
    idx = rng.integers(0, len(d), (B, len(d)))
    r = s[idx].sum(1) / m[idx].sum(1)
    return np.percentile(r, 2.5), np.percentile(r, 97.5), len(d)


def main():
    folder = Path(sys.argv[1])
    use_db = len(sys.argv) > 2 and sys.argv[2] == "db"
    if use_db:
        data = load_db()
        mk = market({"SPY": data["SPY"]}) if "SPY" in data else None
    else:
        data = load_yf(folder / "yf.pkl")
        mk = market(data)
    parts = [feats(t, g, mk) for t, g in data.items()]
    G = pd.concat(parts).sort_index()
    G.index.name = "day"
    print("tickers", G.tk.nunique(), "filas", len(G), G.index.min().date(), G.index.max().date())
    Y = ["a10", "win", "h21", "h35", "mae21"]
    dayavg = G.groupby(level=0)[Y].transform("mean")
    rng = np.random.default_rng(1)
    base = G[Y].mean()
    print("BASE (todas las filas):", {k: round(v * (1 if k == "mae21" else 100), 1) for k, v in base.items()})

    def row(nm, m, lv=None):
        mask = np.asarray(m, bool)
        z = G[mask]
        if len(z) < 30:
            print(f"{nm:48s} n={len(z)} (muestra insuficiente)")
            return
        dd = dayavg[mask]
        out = [f"{nm:48s} n={len(z):6d} d={z.index.nunique():5d}"]
        for y in Y:
            v = z[y].mean()
            diff = z[y].to_numpy() - dd[y].to_numpy()
            lo, hi, _ = boot_day(diff, z.index.to_numpy(), rng)
            sc = 1 if y == "mae21" else 100
            out.append(f"{y} {v*sc:5.1f} ({np.nanmean(diff)*sc:+4.1f} [{lo*sc:+4.1f},{hi*sc:+4.1f}])")
        if lv:
            c = z[lv].dropna()
            out.append(f"niv21 {c.mean()*100:4.1f}")
        h1 = z.index < "2019-01-01"
        out.append(f"| win 04-18 {z.win[h1].mean()*100:4.1f}(n={h1.sum()}) 19-26 {z.win[~h1].mean()*100:4.1f}(n={(~h1).sum()})")
        print(" ".join(out))

    print("\n  formato: métrica valor (diferencia frente a la media de todos los tickers del mismo día [IC95 % por días])\n")
    mkc = G.mkt.to_numpy(bool)
    for nm, col, lv in (("S10 Schwartz EMA10 (toque)", "s10", "lv21_s10"), ("S10 primer toque", "s10_first", "lv21_s10"),
                        ("S10 + SMA50>SMA200 y >SMA200", "s10_ma", "lv21_s10"), ("control: tendencia EMA10 sin toque", "nt10", None),
                        ("placebo: nivel EMA10-1 ATR", "plc", None)):
        row(nm, G[col].to_numpy(bool))
        if col in ("s10", "s10_first", "s10_ma"):
            row(nm + " + mercado alcista", G[col].to_numpy(bool) & mkc)
    row("S8 8 EMA (completo)", G.s8.to_numpy(bool), "lv21_s8")
    row("S8 sin volumen/compresión", G.s8_all.to_numpy(bool), "lv21_s8")
    row("S8 + mercado alcista", G.s8.to_numpy(bool) & mkc)
    row("control S8: ya sobre EMA8 sin pullback", G.s8_ctl.to_numpy(bool))
    for w in (20, 55):
        row(f"D5 primer toque SMA5 (ruptura {w})", G[f"d5_{w}"].to_numpy(bool), "lv21_d5")
        for lo, hi in ((2, 5), (6, 10)):
            row(f"   días desde ruptura {lo}-{hi}", (G[f"d5_{w}"] & G[f"d5_{w}_since"].between(lo, hi)).to_numpy(bool))
        row("   no primer toque (2.º o posterior)", G[f"d5_{w}_any"].to_numpy(bool))
        row("   primer toque + mercado alcista", G[f"d5_{w}"].to_numpy(bool) & mkc)


if __name__ == "__main__":
    main()
