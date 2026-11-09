"""Estudio descriptivo (2026-11-09): qué tickers pasan más tiempo por encima de sus medias y de cuáles. Solo lectura de data/app.db.

Uso: .venv/Scripts/python scripts/suelos/medias_tiempo.py CARPETA [yf]
  Sin `yf`: velas de data/app.db (2.3xx tickers, 2023-2026). Con `yf`: CARPETA/yf.pkl (27 tickers, 2005-2026) para la persistencia a largo plazo.
Escribe CARPETA/tiempo_medias.csv (una fila por ticker) y muestra: % de sesiones con cierre sobre cada media (mediana de tickers),
ranking de tickers, por sector, y persistencia (correlación de Spearman del % sobre cada media entre la primera y la segunda mitad).
Se evalúa desde que existe la SMA200 (mismo periodo para todas las medias). «Rachas» = sesiones seguidas sobre la media; «cruces/año» =
veces que el cierre pasa de arriba a abajo por año (cuanto menos, más limpia la tendencia).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from medias_reglas import load_db, load_yf      # noqa: E402

MAS = {"EMA8": ("e", 8), "EMA10": ("e", 10), "EMA21": ("e", 21), "EMA50": ("e", 50),
       "SMA20": ("s", 20), "SMA50": ("s", 50), "SMA100": ("s", 100), "SMA200": ("s", 200)}


def ma(c, kind, n):
    s = pd.Series(c)
    return (s.ewm(span=n, adjust=False).mean() if kind == "e" else s.rolling(n).mean()).to_numpy()


def sp(a, b):
    """Spearman = Pearson de los rangos (sin scipy)."""
    return float(pd.Series(a).astype(float).rank().corr(pd.Series(b).astype(float).rank()))


def runs(above):
    """(longitud media de racha sobre la media, nº de cruces de arriba a abajo)."""
    a = above.astype(int)
    d = np.diff(a)
    downs = int((d == -1).sum())
    ups = int((d == 1).sum()) + int(a[0] == 1)
    return (a.sum() / ups if ups else 0.0), downs


def per_ticker(g, start_skip=200):
    c = g["close"].to_numpy(float)
    n = len(c)
    seg = slice(start_skip, n)
    mid = (n + start_skip) // 2
    out = {"n": n - start_skip}
    allab = np.ones(n - start_skip, bool)
    for k, (kind, w) in MAS.items():
        m = ma(c, kind, w)
        ab = (c > m)[seg]
        out[f"pct_{k}"] = ab.mean() * 100
        out[f"h1_{k}"] = (c > m)[start_skip:mid].mean() * 100
        out[f"h2_{k}"] = (c > m)[mid:].mean() * 100
        run, dn = runs(ab)
        out[f"racha_{k}"] = run
        out[f"cruces_{k}"] = dn / ((n - start_skip) / 252)
        allab &= ab
    out["pct_todas"] = allab.mean() * 100
    out["ret_pct"] = (c[-1] / c[start_skip] - 1) * 100
    r = np.diff(np.log(c[start_skip - 1:]))
    out["vol_anual"] = r.std() * np.sqrt(252) * 100
    return out


def main():
    folder = Path(sys.argv[1])
    use_yf = len(sys.argv) > 2 and sys.argv[2] == "yf"
    data = load_yf(folder / "yf.pkl") if use_yf else load_db()
    rows = {t: per_ticker(g) for t, g in data.items() if len(g) >= 330}
    R = pd.DataFrame(rows).T
    if not use_yf:
        con = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
        sec = pd.read_sql_query("SELECT ticker, sector FROM ticker_info", con).set_index("ticker").sector
        R["sector"] = sec.reindex(R.index)
    out = folder / ("tiempo_medias_yf.csv" if use_yf else "tiempo_medias.csv")
    R.to_csv(out, float_format="%.2f")
    pc = [f"pct_{k}" for k in MAS]
    print(f"tickers {len(R)}  sesiones evaluadas (mediana) {int(R.n.median())}  -> {out}")
    print("\n% de sesiones con cierre sobre cada media (todos los tickers):")
    t = pd.DataFrame({"mediana": R[pc].median(), "p10": R[pc].quantile(.1), "p90": R[pc].quantile(.9),
                      "racha media (ses.)": [R[f"racha_{k}"].median() for k in MAS], "cruces/año": [R[f"cruces_{k}"].median() for k in MAS]})
    t.index = list(MAS)
    print(t.round(1).to_string())
    print(f"\nsobre las 8 medias a la vez: mediana {R.pct_todas.median():.1f} %, p90 {R.pct_todas.quantile(.9):.1f} %")
    R["media_pct"] = R[pc].mean(axis=1)
    print("\nCorrelación (Spearman) del % sobre la media con el retorno del periodo y con la volatilidad:")
    print(pd.DataFrame({"ret": [sp(R.media_pct, R.ret_pct)], "vol": [sp(R.media_pct, R.vol_anual)]}).round(2).to_string(index=False))
    print("\nPersistencia: Spearman del % sobre la media entre la 1.ª y la 2.ª mitad del periodo:")
    print({k: round(sp(R[f"h1_{k}"], R[f"h2_{k}"]), 2) for k in MAS})
    h1 = R[[f"h1_{k}" for k in MAS]].astype(float).mean(axis=1)
    h2 = R[[f"h2_{k}" for k in MAS]].astype(float).mean(axis=1)
    q = pd.qcut(h1.rank(method="first"), 5, labels=False)
    print("\nQuintiles de la 1.ª mitad (0 = menos tiempo sobre medias) -> % medio sobre medias en la 1.ª y 2.ª mitad:")
    print(pd.DataFrame({"h1": h1.groupby(q).mean(), "h2": h2.groupby(q).mean()}).round(1).to_string())
    cols = ["media_pct", "pct_todas", "pct_EMA10", "pct_SMA50", "pct_SMA200", "cruces_EMA10", "ret_pct", "vol_anual"] + ([] if use_yf else ["sector"])
    print("\nTop 25 por % medio sobre las 8 medias:")
    print(R.sort_values("media_pct", ascending=False)[cols].head(25).round(1).to_string())
    print("\nBottom 10:")
    print(R.sort_values("media_pct")[cols].head(10).round(1).to_string())
    if not use_yf:
        print("\nPor sector (mediana de tickers):")
        print(R.groupby("sector")[["media_pct", "pct_EMA10", "pct_SMA200", "pct_todas", "ret_pct", "vol_anual"]].median().round(1)
              .join(R.groupby("sector").size().rename("n")).sort_values("media_pct", ascending=False).to_string())
        print("\nTickers con ≥ 90 % sobre la SMA200:", int((R.pct_SMA200 >= 90).sum()), "| ≥ 90 % sobre EMA10:", int((R.pct_EMA10 >= 90).sum()))


if __name__ == "__main__":
    main()
