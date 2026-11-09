"""Ronda R13 (2026-11-09): medias móviles como MEDIDA de la tendencia del ticker (no como soporte). Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/medias_tendencia.py CARPETA        # necesita CARPETA/yf.pkl (fetch_yf.py)
     .venv/Scripts/python scripts/suelos/medias_tendencia.py CARPETA db     # velas de data/app.db (solo lectura)

Pregunta: ¿los tickers con un patrón de tendencia alcista medido con medias (persistencia sobre la EMA10, medias apiladas y
crecientes, pendiente y regularidad de la SMA50, extensión sobre la EMA21) tienen mejor resultado posterior que el resto de tickers
el MISMO día? Se evalúa cada día-ticker (entrada al cierre de t; las características usan datos hasta t, causal).
Métricas de medias_reglas.py: a10, win, h21, h35 (put a 2 ATR aguanta), mae21; más ret21 (retorno a 21 sesiones, %).
Se reporta también el gradiente por cubos de cada medida y el efecto de combinarlas con «no extendido» (sobre EMA21 <= 2 ATR).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from medias_reglas import boot_day, feats, load_db, load_yf, market      # noqa: E402


def trend_feats(g):
    c = g["close"].to_numpy(float)
    h, l = g["high"].to_numpy(float), g["low"].to_numpy(float)
    n = len(c)
    S = pd.Series
    e8 = S(c).ewm(span=8, adjust=False).mean().to_numpy()
    e10 = S(c).ewm(span=10, adjust=False).mean().to_numpy()
    e21 = S(c).ewm(span=21, adjust=False).mean().to_numpy()
    s50 = S(c).rolling(50).mean().to_numpy()
    s200 = S(c).rolling(200).mean().to_numpy()
    pc = np.r_[c[0], c[:-1]]
    a = S(np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    T = pd.DataFrame(index=g.index)
    T["pers10"] = S((c > e10).astype(float)).rolling(20).mean().to_numpy() * 100            # % de 20 sesiones con cierre sobre EMA10
    T["pers21"] = S((c > e21).astype(float)).rolling(50).mean().to_numpy() * 100            # % de 50 sesiones sobre EMA21
    T["apil"] = ((e8 > e21) & (e21 > s50) & (s50 > s200)) & (e21 > np.r_[np.full(10, np.nan), e21[:-10]]) & (s50 > np.r_[np.full(20, np.nan), s50[:-20]])
    T["slope50"] = (s50 / np.r_[np.full(20, np.nan), s50[:-20]] - 1) * 100                  # % de subida de la SMA50 en 20 sesiones
    # regularidad: R² de la regresión del log-cierre en 50 sesiones, con pendiente positiva
    x = np.arange(50, dtype=float); x -= x.mean()
    lc = np.log(c)
    w = np.lib.stride_tricks.sliding_window_view(lc, 50)
    m = w.mean(1)
    sl = (w * x).sum(1) / (x * x).sum()
    ss_tot = ((w - m[:, None]) ** 2).sum(1)
    ss_res = ((w - m[:, None] - sl[:, None] * x) ** 2).sum(1)
    r2 = np.where(ss_tot > 0, 1 - ss_res / ss_tot, np.nan)
    T["r2"] = np.r_[np.full(49, np.nan), np.where(sl > 0, r2, 0.0)]
    T["ext21"] = (c - e21) / a                                                              # extensión sobre EMA21 en ATR
    T["above200"] = c > s200
    return T


def forward_ret(g):
    c = g["close"].to_numpy(float)
    return np.r_[(c[21:] / c[:-21] - 1) * 100, np.full(21, np.nan)]


def main():
    folder = Path(sys.argv[1])
    use_db = len(sys.argv) > 2 and sys.argv[2] == "db"
    data = load_db() if use_db else load_yf(folder / "yf.pkl")
    mk = market(data)
    parts = []
    for t, g in data.items():
        F = feats(t, g, mk)
        T = trend_feats(g)
        F = F[["c", "a", "a10", "win", "h21", "h35", "mae21", "mc21", "tk"]].join(T)
        F["ret21"] = pd.Series(forward_ret(g), index=g.index).reindex(F.index)
        parts.append(F)
    G = pd.concat(parts).sort_index()
    G.index.name = "day"
    G = G.dropna(subset=["pers10", "r2", "slope50", "ext21"])
    G["p10"] = np.where(G.mc21.isna(), np.nan, (G.mc21 >= 0.90 * G.c).astype(float))      # colchón fijo: ningún cierre bajo -10 % en 21 sesiones
    Y = ["a10", "win", "h21", "h35", "p10", "ret21", "mae21"]
    dayavg = G.groupby(level=0)[Y].transform("mean")
    rng = np.random.default_rng(1)
    print("tickers", G.tk.nunique(), "filas", len(G), G.index.min().date(), G.index.max().date())
    print("BASE:", {k: round(G[k].mean() * (1 if k in ("mae21", "ret21") else 100), 1) for k in Y})
    print("\n  formato: valor (dif. frente a la media de todos los tickers del mismo día [IC95 % por días])\n")

    def row(nm, mask):
        mask = np.asarray(mask, bool)
        z = G[mask]
        if len(z) < 50:
            print(f"{nm:50s} n={len(z)} (insuficiente)")
            return
        dd = dayavg[mask]
        out = [f"{nm:50s} n={len(z):7d}"]
        for y in Y:
            diff = z[y].to_numpy() - dd[y].to_numpy()
            lo, hi, _ = boot_day(diff, z.index.to_numpy(), rng, 1000)
            sc = 1 if y in ("mae21", "ret21") else 100
            out.append(f"{y} {z[y].mean()*sc:5.1f} ({np.nanmean(diff)*sc:+4.1f} [{lo*sc:+4.1f},{hi*sc:+4.1f}])")
        h1 = z.index < "2019-01-01"
        if h1.any() and (~h1).any():
            out.append(f"| h21 dif 04-18 {np.nanmean(z.h21[h1]-dd.h21[h1])*100:+4.1f} 19-26 {np.nanmean(z.h21[~h1]-dd.h21[~h1])*100:+4.1f}")
        print(" ".join(out))

    ne = (G.ext21 <= 2).to_numpy()
    print("-- persistencia sobre EMA10 (% de las últimas 20 sesiones)")
    for lo, hi in ((0, 40), (40, 70), (70, 90), (90, 101), (100, 101)):
        row(f"pers10 {lo}-{hi}", ((G.pers10 >= lo) & (G.pers10 < hi)).to_numpy())
    print("-- persistencia sobre EMA21 (% de 50 sesiones)")
    for lo, hi in ((0, 50), (50, 80), (80, 95), (95, 101)):
        row(f"pers21 {lo}-{hi}", ((G.pers21 >= lo) & (G.pers21 < hi)).to_numpy())
    print("-- medias apiladas y crecientes (EMA8>EMA21>SMA50>SMA200)")
    row("apiladas y crecientes", G.apil.to_numpy(bool))
    row("no apiladas", ~G.apil.to_numpy(bool))
    row("apiladas, no extendido (ext21 <= 2 ATR)", G.apil.to_numpy(bool) & ne)
    print("-- pendiente SMA50 (% en 20 sesiones)")
    for lo, hi in ((-99, -2), (-2, 0), (0, 2), (2, 5), (5, 99)):
        row(f"slope50 {lo}..{hi}", ((G.slope50 >= lo) & (G.slope50 < hi)).to_numpy())
    print("-- regularidad: R² del log-precio a 50 sesiones (pendiente positiva)")
    for lo, hi in ((0, .3), (.3, .6), (.6, .8), (.8, .9), (.9, 1.01)):
        row(f"r2 {lo}-{hi}", ((G.r2 > lo) & (G.r2 <= hi) | ((lo == 0) & (G.r2 == 0))).to_numpy())
    print("-- extensión sobre EMA21 (ATR)")
    for lo, hi in ((-99, -1), (-1, 0), (0, 1), (1, 2), (2, 3), (3, 99)):
        row(f"ext21 {lo}..{hi}", ((G.ext21 >= lo) & (G.ext21 < hi)).to_numpy())
    print("-- combinaciones")
    top = ((G.pers10 >= 90) & G.apil & (G.r2 >= 0.8)).to_numpy()
    row("pers10>=90 + apiladas + r2>=0,8", top)
    row("   y no extendido", top & ne)
    row("pers10>=90 + apiladas", ((G.pers10 >= 90) & G.apil).to_numpy())
    row("sobre SMA200 solo (control)", G.above200.to_numpy(bool))
    row("bajo SMA200 (control)", ~G.above200.to_numpy(bool))


if __name__ == "__main__":
    main()
