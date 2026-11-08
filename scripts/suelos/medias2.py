"""Medias que «respeta» cada ticker, segunda ronda (2026-11-08). Solo lectura de data/app.db.

Uso: .venv/Scripts/python scripts/suelos/medias2.py [carpeta_salida] [max_tickers]

Prueba si la tasa de rebote de un ticker en una media PERSISTE, variando: ventana de entrenamiento (63/126/252 sesiones), banda de toque
(0,1/0,25/0,5 ATR), confirmación (1/3/5 sesiones), régimen (solo con cierre sobre la SMA200 y SMA50 > SMA200 = «ventana ascendente») y
mínimo de toques. Para cada configuración agrupa los (ticker, media, origen) por su tasa en entrenamiento y mide la tasa en las 63
sesiones siguientes: si la tasa de entrenamiento alta no sigue alta fuera de muestra, no hay persistencia.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from medias_soportes import MAS, atr14, load, ma_series      # noqa: E402

TEST = 63
ORIGINS = 8
TRAINS = (63, 126, 252)
TOLS = (0.1, 0.25, 0.5)
HOLDS = (1, 3, 5)
REGIMES = ("todos", "alcista")
MIN_T = 3
BUCKETS = [(0, .4), (.4, .6), (.6, .8), (.8, 1.01)]


def events(h, l, c, a, ma, regime_ok, tol, hold):
    n = len(c)
    ok = ~np.isnan(ma)
    near = (l <= ma + tol * a) & ok
    prev_above = np.r_[False, c[:-1] > ma[:-1]]
    approach = np.r_[True, ~near[:-1]]
    rising = np.r_[np.zeros(10, bool), ma[10:] > ma[:-10]]
    idx = np.where(near & prev_above & approach & rising & ok & regime_ok)[0]
    idx = idx[idx + hold < n]
    res = (c[idx] >= ma[idx]) & (c[idx + hold] > ma[idx + hold])
    return idx, res


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out.mkdir(parents=True, exist_ok=True)
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    data = load(Path("data/app.db"), limit)
    print(len(data), "tickers", flush=True)
    rows = []        # una fila por (config, ticker, media, origen) con >= MIN_T toques en entrenamiento
    for t, g in data.items():
        h, l, c = g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float)
        a = atr14(h, l, c)
        n = len(c)
        s200 = ma_series(c, "s", 200)
        s50 = ma_series(c, "s", 50)
        alc = np.nan_to_num((c > s200) & (s50 > s200), nan=0).astype(bool)
        reg = {"todos": np.ones(n, bool), "alcista": np.r_[False, alc[:-1]]}        # régimen del día ANTERIOR: el del mismo día filtra los rebotes (look-ahead)
        series = {k: ma_series(c, *v) for k, v in MAS.items()}
        for tol, hold, rg in itertools.product(TOLS, HOLDS, REGIMES):
            ev = {k: events(h, l, c, a, s, reg[rg], tol, hold) for k, s in series.items()}
            for train in TRAINS:
                for o in range(1, ORIGINS + 1):
                    cut = n - o * TEST
                    if cut - train < 30:
                        break
                    for k, (idx, res) in ev.items():
                        tr = (idx >= cut - train) & (idx + hold < cut)
                        if tr.sum() < MIN_T:
                            continue
                        te = (idx >= cut) & (idx < cut + TEST)
                        rows.append((tol, hold, rg, train, k, t, int(tr.sum()), float(res[tr].mean()), int(te.sum()), int(res[te].sum())))
    R = pd.DataFrame(rows, columns=["tol", "hold", "reg", "train", "ma", "ticker", "t_tr", "r_tr", "t_te", "b_te"])
    R.to_pickle(out / "medias2.pkl")
    L = []

    def tasa(d):
        return d.b_te.sum() / d.t_te.sum() if d.t_te.sum() else float("nan")

    L.append("Tasa de rebote fuera de muestra por tramo de tasa de ENTRENAMIENTO (toques en test entre paréntesis).")
    L.append("Si persistiera, los tramos altos deberían dar tasas claramente mayores que los bajos.\n")
    for (tol, hold, rg, train), d in R.groupby(["tol", "hold", "reg", "train"]):
        for minT in (3, 5):
            dd = d[d.t_tr >= minT]
            if len(dd) < 500:
                continue
            parts = [f"{int(lo * 100)}-{int(min(hi, 1) * 100)}%: {tasa(x):.1%} ({int(x.t_te.sum())})" for lo, hi in BUCKETS
                     for x in [dd[(dd.r_tr >= lo) & (dd.r_tr < hi)]] if len(x)]
            L.append(f"tol {tol} hold {hold} {rg:8s} train {train:3d} minT {minT}: " + " | ".join(parts) + f" | total {tasa(dd):.1%}")
    L.append("\nPor media (todas las configuraciones con tol 0,25, hold 3, train 126, minT 3): tasa fuera de muestra / n. de toques")
    d0 = R[(R.tol == 0.25) & (R.hold == 3) & (R.train == 126)]
    for rg, d in d0.groupby("reg"):
        L.append(f" régimen {rg}: " + " | ".join(f"{k} {tasa(x):.1%}" for k, x in d.groupby("ma")))
    # persistencia por ticker y media: la tasa del ticker en el test frente a su tasa en el train, con una media concreta
    L.append("\nEMA8 y EMA9 por separado (tol 0,25, hold 3, train 126), tramo de entrenamiento >= 80% con >= 5 toques:")
    for rg in REGIMES:
        for k in ("EMA8", "EMA9"):
            x = d0[(d0.reg == rg) & (d0.ma == k) & (d0.t_tr >= 5)]
            hi = x[x.r_tr >= .8]
            L.append(f" {rg:8s} {k}: tasa general {tasa(x):.1%} ({int(x.t_te.sum())}) | tramo alto {tasa(hi):.1%} ({int(hi.t_te.sum())})")
    (out / "medias2.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
