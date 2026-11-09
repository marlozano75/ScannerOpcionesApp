"""Ronda R15 (2026-11-09): fuerza relativa (RS estilo IBD, Fred6725/relative-strength) como señal de fortaleza. Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/rs_fortaleza.py CARPETA db     # velas de data/app.db (solo lectura; ~2.300 tickers, 2023-2026)
     .venv/Scripts/python scripts/suelos/rs_fortaleza.py CARPETA        # CARPETA/yf.pkl (fetch_yf.py; 27 tickers 2004-2026: el percentil es poco fiable)

RS: score = 0,4*P3 + 0,2*P6 + 0,2*P9 + 0,2*P12 con rendimientos a 63/126/189/252 sesiones (datos hasta t, causal). El RS final de
Fred6725 es (1+score)/(1+score_SPY): para un mismo día es monotónico en el score, así que el percentil (0-99) es el del score.
Percentil = posición del ticker entre TODOS los tickers de la muestra ese día. Se mide además su tendencia (rs_pct - rs_pct de hace 21 sesiones).

Pregunta: ¿los tickers con RS alto tienen mejor resultado posterior (venta de put) que el resto el MISMO día? ¿Y a igual volatilidad? El
RS correlaciona con la volatilidad: por eso se repite cada tramo con el control «mismo día y mismo tercil de volatilidad a 63 sesiones».
Métricas de medias_reglas.py (a10, win, h21, h35, mae21) más p10 (ningún cierre bajo -10 % en 21 sesiones) y ret21 (retorno a 21 sesiones, %).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from medias_reglas import boot_day, feats, load_db, load_yf, market      # noqa: E402

PER = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


def rs_score(c: pd.Series) -> pd.Series:
    return sum(w * (c / c.shift(k) - 1) for k, w in PER)


def main():
    folder = Path(sys.argv[1])
    use_db = len(sys.argv) > 2 and sys.argv[2] == "db"
    data = load_db() if use_db else load_yf(folder / "yf.pkl")
    if "SPY" not in data:
        sys.exit("falta SPY en los datos")
    mk = market(data)
    parts = []
    for t, g in data.items():
        if t == "SPY":
            continue
        F = feats(t, g, mk)[["c", "a", "a10", "win", "h21", "h35", "mae21", "mc21", "tk"]]
        c = g["close"]
        F["score"] = rs_score(c).reindex(F.index)
        F["vol63"] = (np.log(c).diff().rolling(63).std() * np.sqrt(252) * 100).reindex(F.index)
        cc = c.to_numpy(float)
        F["ret21"] = pd.Series(np.r_[(cc[21:] / cc[:-21] - 1) * 100, np.full(21, np.nan)], index=g.index).reindex(F.index)
        parts.append(F)
    G = pd.concat(parts).sort_index()
    G.index.name = "day"
    G = G.dropna(subset=["score", "vol63"])
    # percentil 0-99 entre los tickers del mismo día (solo días con muestra suficiente)
    n_day = G.groupby(level=0).score.transform("size")
    G = G[n_day >= 20].copy()
    G["rs"] = (G.groupby(level=0).score.rank(pct=True) * 99).round()
    G = G.reset_index().sort_values(["tk", "day"])
    G["rs_chg"] = G["rs"] - G.groupby("tk")["rs"].shift(21)
    G = G.set_index("day").sort_index()
    G["vt"] = G.groupby(level=0).vol63.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    G["p10"] = np.where(G.mc21.isna(), np.nan, (G.mc21 >= 0.90 * G.c).astype(float))
    Y = ["a10", "win", "h21", "h35", "p10", "ret21", "mae21"]
    SC = {y: (1 if y in ("mae21", "ret21") else 100) for y in Y}
    day_all = G.groupby(level=0)[Y].transform("mean")
    day_vol = G.groupby([G.index, "vt"])[Y].transform("mean")
    rng = np.random.default_rng(1)
    print("tickers", G.tk.nunique(), "filas", len(G), G.index.min().date(), G.index.max().date(), "| mediana de tickers por día", int(G.groupby(level=0).size().median()))
    print("BASE:", {k: round(G[k].mean() * SC[k], 1) for k in Y})
    print("\nformato: valor (dif. frente al control [IC95 % por días]); C=mismo día; V=mismo día y mismo tercil de volatilidad\n")

    def row(nm, mask):
        mask = np.asarray(mask, bool)
        z = G[mask]
        if len(z) < 100:
            print(f"{nm:34s} n={len(z)} (insuficiente)")
            return
        out = [f"{nm:34s} n={len(z):7d} vol63 {z.vol63.mean():4.0f}"]
        for y in Y:
            for tag, ctl in (("C", day_all), ("V", day_vol)):
                diff = z[y].to_numpy() - ctl[mask][y].to_numpy()
                if tag == "C" or y in ("h21", "p10", "ret21"):
                    lo, hi, _ = boot_day(diff, z.index.to_numpy(), rng, 500)
                    out.append(f"{y}{tag} {z[y].mean()*SC[y]:5.1f} ({np.nanmean(diff)*SC[y]:+4.1f} [{lo*SC[y]:+4.1f},{hi*SC[y]:+4.1f}])")
        yrs = z.index.year
        late = yrs >= yrs.max() - 1
        if late.any() and (~late).any():
            d = z.h21.to_numpy() - ctl_v(mask)
            out.append(f"| h21 V-dif {np.nanmean(d[~late])*100:+4.1f} / {np.nanmean(d[late])*100:+4.1f} (resto / últimos 2 años)")
        print(" ".join(out))

    def ctl_v(mask):
        return day_vol[mask].h21.to_numpy()

    print("-- quintiles de percentil RS")
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        row(f"RS {lo}-{hi - 1 if hi < 100 else 99}", ((G.rs >= lo) & (G.rs < hi if hi < 100 else G.rs >= lo)).to_numpy())
    print("-- tramos finos de la cima")
    for lo, hi in ((90, 95), (95, 100), (98, 100)):
        row(f"RS {lo}-99", ((G.rs >= lo) & (G.rs < hi if hi < 100 else G.rs >= lo)).to_numpy())
    print("-- RS >= 80 según tercil de volatilidad (¿ventaja a igual volatilidad?)")
    for v, nm in ((0, "baja"), (1, "media"), (2, "alta")):
        row(f"RS>=80, vol {nm}", ((G.rs >= 80) & (G.vt == v)).to_numpy())
        row(f"RS<40,  vol {nm}", ((G.rs < 40) & (G.vt == v)).to_numpy())
    print("-- tendencia del RS (RS actual - RS hace 21 sesiones), con RS >= 70")
    top = (G.rs >= 70).to_numpy()
    for lo, hi in ((-99, -10), (-10, 10), (10, 99)):
        row(f"RS>=70, cambio {lo}..{hi}", top & ((G.rs_chg >= lo) & (G.rs_chg < hi)).to_numpy())
    print("-- RS alto pero ya cayendo / bajo pero subiendo")
    row("RS>=80 y RS bajó >=15", ((G.rs >= 80) & (G.rs_chg <= -15)).to_numpy())
    row("RS<40 y RS subió >=15", ((G.rs < 40) & (G.rs_chg >= 15)).to_numpy())
    print("-- con mercado alcista (SPY) / no alcista")
    mkt = mk.reindex(G.index).shift(1).fillna(False).to_numpy(bool)
    row("RS>=80, mercado alcista", (G.rs >= 80).to_numpy() & mkt)
    row("RS>=80, mercado no alcista", (G.rs >= 80).to_numpy() & ~mkt)
    row("RS<40, mercado no alcista", (G.rs < 40).to_numpy() & ~mkt)


if __name__ == "__main__":
    main()
