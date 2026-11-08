"""Medias y soportes que «respeta» cada ticker (2026-11-08). Solo lectura de data/app.db (velas diarias OHLC).

Uso: .venv/Scripts/python scripts/suelos/medias_soportes.py [carpeta_salida] [max_tickers]

1) MEDIAS (EMA8, EMA9, EMA20, EMA50, SMA20, SMA50, SMA100, SMA200): un TOQUE es un día en que el mínimo llega a la media (+0,25 ATR)
   viniendo de un cierre por encima, con la media subiendo; REBOTE si ese día cierra por encima de la media y 3 sesiones después
   sigue por encima. Puntuación = rebotes / toques (mínimo `MIN_TOUCHES`).
2) SOPORTES horizontales: mínimos locales (pivotes de ±5 velas) agrupados en una banda de `BAND_PCT` %, nunca perdidos (ningún cierre
   por debajo de la banda después del primer pivote). Toques = pivotes del grupo; rebotes = aproximaciones posteriores que aguantan.
3) VALIDACIÓN fuera de muestra: con datos hasta T se elige la media / se listan los soportes, y se mide qué pasa en las 63 sesiones
   siguientes (6 orígenes no solapados). Compara la media «más respetada» con la media de todas, y la tasa de rebote del soporte
   según sus toques previos.
Escribe medias.csv, soportes.csv (estado actual por ticker) y valida.txt en la carpeta de salida.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAS = {"EMA8": ("e", 8), "EMA9": ("e", 9), "EMA20": ("e", 20), "EMA50": ("e", 50),
       "SMA20": ("s", 20), "SMA50": ("s", 50), "SMA100": ("s", 100), "SMA200": ("s", 200)}
MIN_TOUCHES = 4
TOL_ATR = 0.25
HOLD = 3
TEST = 63
ORIGINS = 6
TRAIN = 252
WIDTH = 5
BAND_PCT = 1.5


def load(db: Path, limit: int | None) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT ticker, day, open, high, low, close FROM daily_bars WHERE provisional = 0 AND high IS NOT NULL "
                           "AND low IS NOT NULL ORDER BY ticker, day", con)
    out = {}
    for t, g in df.groupby("ticker"):
        g = g.reset_index(drop=True)
        jumps = np.where(np.abs(g.close.pct_change().to_numpy()) > 0.4)[0]       # splits sin ajustar: se queda lo posterior
        if len(jumps):
            g = g.iloc[jumps[-1]:].reset_index(drop=True)
        if len(g) >= TRAIN + TEST + 60:
            out[t] = g
        if limit and len(out) >= limit:
            break
    return out


def atr14(h, l, c):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()


def ma_series(c, kind, n):
    s = pd.Series(c)
    return (s.ewm(span=n, adjust=False).mean() if kind == "e" else s.rolling(n).mean()).to_numpy()


def ma_events(h, l, c, a, ma):
    """Índices de toque y si rebotan (None si aún no se sabe)."""
    n = len(c)
    ok = ~np.isnan(ma)
    near = (l <= ma + TOL_ATR * a) & ok
    prev_above = np.r_[False, c[:-1] > ma[:-1]]
    approach = np.r_[True, ~near[:-1]]
    rising = np.r_[np.zeros(10, bool), ma[10:] > ma[:-10]]
    idx = np.where(near & prev_above & approach & rising & ok)[0]
    idx = idx[idx + HOLD < n]
    res = (c[idx] >= ma[idx]) & (c[idx + HOLD] > ma[idx + HOLD])
    return idx, res


def pivots_low(l, width):
    out = []
    for i in range(width, len(l) - width):
        if l[i] < l[i - width:i].min() and l[i] <= l[i + 1:i + 1 + width].min():
            out.append(i)
    return out


def build_levels(h, l, c, end):
    """Soportes con datos hasta `end` (exclusivo): lista de (nivel_bajo, nivel_alto, n_pivotes, primer_idx, ultimo_idx)."""
    piv = pivots_low(l[:end], WIDTH)
    groups: list[list[int]] = []
    for i in sorted(piv, key=lambda k: l[k]):
        if groups and l[i] <= l[groups[-1][0]] * (1 + BAND_PCT / 100):
            groups[-1].append(i)
        else:
            groups.append([i])
    levels = []
    for g in groups:
        lo, hi = min(l[i] for i in g), max(l[i] for i in g)
        first = min(g)
        if (c[first + 1:end] < lo * (1 - BAND_PCT / 100)).any():                 # perdido
            continue
        levels.append((lo, hi, len(g), first, max(g)))
    return levels


def level_events(l, c, lo, hi, start, end):
    """Aproximaciones al soporte entre start y end: (idx, rebota). Rebota = cierra sobre `lo` y 3 sesiones después sigue sin perderlo."""
    n = len(c)
    near = l <= hi * (1 + 0.003)
    res = []
    for t in range(max(start, 1), min(end, n - HOLD)):
        if near[t] and not near[t - 1] and c[t - 1] > hi:
            res.append((t, bool(c[t] >= lo and c[t + HOLD] > lo * (1 - BAND_PCT / 200))))
    return res


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out.mkdir(parents=True, exist_ok=True)
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    data = load(Path("data/app.db"), limit)
    print(len(data), "tickers")
    rows_ma, rows_sup = [], []
    val_ma = []        # (elegida_rebote, elegida_toques, todas_rebotes, todas_toques)
    val_sup = []       # (n_pivotes_previos, rebota)
    for t, g in data.items():
        h, l, c = g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float)
        a = atr14(h, l, c)
        n = len(c)
        series = {k: ma_series(c, *v) for k, v in MAS.items()}
        ev = {k: ma_events(h, l, c, a, s) for k, s in series.items()}
        # estado actual: ventanas de 6, 12 y 36 meses
        for months in (6, 12, 36):
            start = n - int(months * 21)
            for k, (idx, res) in ev.items():
                m = idx >= start
                tt, bb = int(m.sum()), int(res[m].sum())
                if tt:
                    rows_ma.append(dict(ticker=t, meses=months, media=k, toques=tt, rebotes=bb, tasa=bb / tt, valor=series[k][-1],
                                        dist_pct=(c[-1] / series[k][-1] - 1) * 100, ultimo_toque=g.day[idx[m][-1]]))
        # validación fuera de muestra
        for o in range(1, ORIGINS + 1):
            cut = n - o * TEST
            if cut - TRAIN < 60:
                break
            best, best_s = None, -1.0
            tot_b = tot_t = 0
            for k, (idx, res) in ev.items():
                tr = (idx >= cut - TRAIN) & (idx + HOLD < cut)
                if tr.sum() >= MIN_TOUCHES:
                    s = res[tr].mean()
                    if s > best_s or (s == best_s and tr.sum() > best[1]):
                        best, best_s = (k, int(tr.sum())), s
                te = (idx >= cut) & (idx < cut + TEST)
                tot_t += int(te.sum()); tot_b += int(res[te].sum())
            if best:
                idx, res = ev[best[0]]
                te = (idx >= cut) & (idx < cut + TEST)
                val_ma.append((int(res[te].sum()), int(te.sum()), tot_b, tot_t, best_s))
            for lo, hi, npiv, first, last in build_levels(h, l, c, cut):
                if hi < c[cut - 1]:
                    for _, ok in level_events(l, c, lo, hi, cut, cut + TEST):
                        val_sup.append((npiv, ok))
        # soportes actuales
        for lo, hi, npiv, first, last in build_levels(h, l, c, n):
            if hi < c[-1]:
                evs = level_events(l, c, lo, hi, first + WIDTH + 1, n)
                rows_sup.append(dict(ticker=t, nivel_bajo=lo, nivel_alto=hi, pivotes=npiv, aproximaciones=len(evs),
                                     rebotes=sum(1 for _, ok in evs if ok), primer_dia=g.day[first], ultimo_pivote=g.day[last],
                                     dist_pct=(c[-1] / hi - 1) * 100))
    pd.DataFrame(rows_ma).to_csv(out / "medias.csv", index=False)
    pd.DataFrame(rows_sup).to_csv(out / "soportes.csv", index=False)
    v = pd.DataFrame(val_ma, columns=["b_el", "t_el", "b_all", "t_all", "train"])
    lines = [f"MEDIAS — validación fuera de muestra: {len(v)} (ticker, origen) con una media elegida (>= {MIN_TOUCHES} toques en {TRAIN} sesiones previas)",
             f"  tasa de rebote en las {TEST} sesiones siguientes: media elegida {v.b_el.sum() / max(v.t_el.sum(), 1):.1%} "
             f"({v.t_el.sum()} toques) | las 8 medias juntas {v.b_all.sum() / max(v.t_all.sum(), 1):.1%} ({v.t_all.sum()} toques)",
             f"  tasa en entrenamiento de la elegida: {v.train.mean():.1%} (si cae mucho fuera de muestra, la «preferida» era azar)"]
    s = pd.DataFrame(val_sup, columns=["npiv", "ok"])
    s["grupo"] = s.npiv.clip(upper=4)
    lines.append(f"SOPORTES — {len(s)} aproximaciones fuera de muestra, tasa de rebote por pivotes previos del nivel:")
    for gk, gg in s.groupby("grupo"):
        lines.append(f"  {'4+' if gk == 4 else gk} pivotes: {gg.ok.mean():.1%} ({len(gg)})")
    (out / "valida.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
