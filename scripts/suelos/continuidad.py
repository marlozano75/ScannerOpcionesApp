"""Ronda R17 (2026-11-15): indicadores de CONTINUIDAD de un movimiento al alza. Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/continuidad.py CARPETA            # CARPETA/yf.pkl (fetch_yf.py): 27 tickers
     .venv/Scripts/python scripts/suelos/continuidad.py CARPETA val        # además CARPETA/yf_val_ok.pkl (validacion_fetch.py
                                                                          # + validacion_filtro.py): 2.179 tickers
Pregunta: con un impulso alcista YA en marcha, ¿qué indicadores técnicos separan los impulsos que siguen subiendo de los que
se agotan? A diferencia de R5 (señales como entrada en frío) aquí la muestra son solo los impulsos y el control principal son
los impulsos SIN el indicador.

Eventos (cierre de t, causal; solo el primer día de cada impulso: ningún evento del mismo tipo en las 10 sesiones previas):
  imp  = el cierre sube >= 2 ATR14 en 5 sesiones (c_t - c_t-5 >= 2·ATR_t)
  brk  = cierre por encima del máximo de las 20 sesiones anteriores
Resultados desde el cierre de t: win10 (carrera +1,5 ATR antes de -1,5 ATR en 10 sesiones), a5/a10/a20 (cierra por encima a
5/10/20 sesiones), r20 (retorno a 20 sesiones, %), h21 (ningún cierre bajo entrada - 2 ATR en 21 sesiones: aguante de una put).
Se compara cada indicador dentro del evento con el resto del mismo evento: diferencia y su IC95 % por bootstrap de días, en
2004-2018 (descubrimiento) y 2019-2026 (validación), y con un voto por día.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

Y_COLS = ["win10", "a5", "a10", "a20", "r20", "h21"]
SPLIT = np.datetime64("2019-01-01")


def ld(Y, skip_idx=True):
    out = {}
    for t in Y.columns.get_level_values(0).unique():
        if t.startswith("^"):
            continue
        x = Y[t].dropna(subset=["Close"])
        x = x[(x.High >= x.Low) & (x.Close > 0)]
        if len(x) < 400:
            continue
        out[t] = x
    return out


def fwd(a, k):
    """a[t+k] (NaN al final)."""
    return np.r_[a[k:], np.full(k, np.nan)]


def bk(a, k):
    """a[t-k] (NaN al principio)."""
    return np.r_[np.full(k, np.nan), a[:-k]] if k else a


def fwd_min(a, k):
    """mínimo de a[t+1 .. t+k]."""
    s = pd.Series(a[::-1]).rolling(k, min_periods=k).min().to_numpy()[::-1]
    return np.r_[s[1:], np.nan]


def ema(x, span):
    return pd.Series(x).ewm(span=span, adjust=False).mean().to_numpy()


def wilder(x, n=14):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def first_only(ev, gap=10):
    prev = pd.Series(ev.astype(float)).shift(1).rolling(gap, min_periods=1).max().fillna(0).to_numpy() > 0
    return ev & ~prev


def ticker(t, x, mk):
    o, h, l, c, v = (x[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    v = np.nan_to_num(v)
    n = len(c)
    pc = bk(c, 1)
    tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    a = wilder(np.nan_to_num(tr, nan=h[0] - l[0]))
    R = pd.Series
    # ---------- resultados
    first_up = np.full(n, 99); first_dn = np.full(n, 99)
    for k in range(10, 0, -1):
        first_up = np.where(fwd(h, k) >= c + 1.5 * a, k, first_up)
        first_dn = np.where(fwd(l, k) <= c - 1.5 * a, k, first_dn)
    ok = lambda k: np.arange(n) + k < n
    out = {
        "win10": np.where(ok(10), (first_up < first_dn), np.nan),
        "a5": np.where(ok(5), fwd(c, 5) > c, np.nan),
        "a10": np.where(ok(10), fwd(c, 10) > c, np.nan),
        "a20": np.where(ok(20), fwd(c, 20) > c, np.nan),
        "r20": (fwd(c, 20) / c - 1) * 100,
    }
    mc21 = fwd_min(c, 21)
    out["h21"] = np.where(np.isnan(mc21), np.nan, mc21 >= c - 2 * a)
    # ---------- eventos
    imp = first_only(np.nan_to_num(c - bk(c, 5)) >= 2 * a)
    hi20 = bk(R(h).rolling(20).max().to_numpy(), 1)
    brk = first_only(np.nan_to_num(c - hi20, nan=-1) > 0)
    # ---------- indicadores (todo con datos hasta t)
    up, dn = h - bk(h, 1), bk(l, 1) - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    trs = wilder(np.nan_to_num(tr, nan=0.0))
    pdi = 100 * wilder(np.nan_to_num(pdm)) / trs; mdi = 100 * wilder(np.nan_to_num(mdm)) / trs
    dx = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi > 0, pdi + mdi, np.nan)
    adx = wilder(np.nan_to_num(dx))
    d = np.diff(c, prepend=c[0])
    rs = wilder(np.maximum(d, 0)) / np.where(wilder(np.maximum(-d, 0)) > 0, wilder(np.maximum(-d, 0)), np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = np.where(np.isnan(rsi), 100.0, rsi)
    macd = ema(c, 12) - ema(c, 26); hist = macd - ema(macd, 9)
    sma20 = R(c).rolling(20).mean().to_numpy(); sd20 = R(c).rolling(20).std().to_numpy()
    ub = sma20 + 2 * sd20
    bw = 4 * sd20 / sma20
    bwq = R(bw).rolling(120).rank(pct=True).to_numpy()
    e21 = ema(c, 21)
    s50 = R(c).rolling(50).mean().to_numpy(); s200 = R(c).rolling(200).mean().to_numpy()
    hi252 = R(h).rolling(252).max().to_numpy()
    rv = v / bk(R(v).rolling(20).mean().to_numpy(), 1)
    rv5 = R(rv).rolling(5).mean().to_numpy()
    obv = np.cumsum(np.sign(d) * v)
    gap = (o > bk(h, 1))
    gap_hold = np.zeros(n, bool)
    for k in range(0, 5):              # gap de los últimos 5 días sin cerrar: mínimo desde el gap >= máximo previo al gap
        gk = bk(gap.astype(float), k) > 0
        gap_hold |= gk & (R(l).rolling(k + 1).min().to_numpy() >= bk(h, k + 1))
    up_days = R((d > 0).astype(float)).rolling(5).sum().to_numpy()
    aroon_up = 100 * (25 - (25 - R(h).rolling(26).apply(np.argmax, raw=True).to_numpy())) / 25
    aroon_dn = 100 * (25 - (25 - R(l).rolling(26).apply(np.argmin, raw=True).to_numpy())) / 25
    rngd = h - l
    clv = np.where(rngd > 0, (c - l) / rngd, 0.5)
    ret63 = c / bk(c, 63) - 1
    spy = mk.reindex(x.index)
    S = {
        "adx25": (adx > 25) & (pdi > mdi),
        "adx_sube": (adx > 20) & (adx > bk(adx, 5)) & (pdi > mdi),
        "adx_alto40": adx > 40,
        "vol_rel_1,5": rv5 >= 1.5,
        "vol_dia_2": rv >= 2,
        "cierre_alto_25": clv >= 0.75,
        "cierre_bajo_50": clv < 0.5,
        "gap_sin_cerrar": gap_hold,
        "rsi_rango_alcista": (R(rsi).rolling(20).min().to_numpy() >= 40) & (rsi <= 80),
        "rsi70_3d": R((rsi > 70).astype(float)).rolling(3).sum().to_numpy() == 3,
        "rsi_80": rsi > 80,
        "macd_hist_crece": (hist > 0) & (hist > bk(hist, 1)) & (bk(hist, 1) > bk(hist, 2)),
        "macd_bajo_cero": macd < 0,
        "squeeze_previo": bk(bwq, 5) <= 0.2,
        "bb_paseo": R((c >= ub).astype(float)).rolling(5).sum().to_numpy() >= 3,
        "cerca_max52_5": c >= 0.95 * hi252,
        "max52_nuevo": c >= hi252,
        "lejos_max52_25": c < 0.75 * hi252,
        "obv_max20": obv >= R(obv).rolling(20).max().to_numpy(),
        "extension_3atr": (c - e21) >= 3 * a,
        "4_de_5_al_alza": up_days >= 4,
        "aroon": (aroon_up >= 100) & (aroon_dn <= 30),
        "medias_apiladas": (c > s50) & (s50 > s200),
        "bajo_sma200": c < s200,
        "rs_spy_63": ret63 > (spy["ret63"].to_numpy()),
        "spy_sobre_sma200": spy["up200"].to_numpy(bool),
        "vix_contango": spy["contango"].to_numpy(bool),
        "vix_backward": spy["backw"].to_numpy(bool),
    }
    warm = np.arange(n) >= 260
    keep = warm & (imp | brk)
    F = pd.DataFrame({k: np.asarray(vv, float)[keep] for k, vv in out.items()}, index=x.index[keep])
    F["imp"] = imp[keep]; F["brk"] = brk[keep]
    for k, vv in S.items():
        F[k] = np.asarray(vv, bool)[keep]
    F["tk"] = t
    # media diaria de todos los días del ticker (control «mismo día, todos los tickers»)
    U = pd.DataFrame({k: np.asarray(vv, float)[warm] for k, vv in out.items()}, index=x.index[warm])
    return F, U


def market(yf):
    s = yf["SPY"]["Close"]
    m = pd.DataFrame(index=s.index)
    m["ret63"] = s / s.shift(63) - 1
    m["up200"] = s > s.rolling(200).mean()
    vix, v3 = yf["^VIX"]["Close"].reindex(s.index), yf["^VIX3M"]["Close"].reindex(s.index)
    m["contango"] = (vix < v3 * 0.95).fillna(False)
    m["backw"] = (vix > v3).fillna(False)
    return m


def build(data, mk):
    parts, usum, ucnt = [], None, None
    for t, x in data.items():
        F, U = ticker(t, x, mk)
        parts.append(F)
        s, nn = U.groupby(level=0).sum(min_count=1), U.notna().astype(float).groupby(level=0).sum()
        usum = s if usum is None else usum.add(s, fill_value=0)
        ucnt = nn if ucnt is None else ucnt.add(nn, fill_value=0)
    G = pd.concat(parts).sort_index()
    G.index.name = "day"
    return G, usum / ucnt


def boot_diff(y, sig, days, rng, B=1000):
    """IC95 % de mean(y|sig) - mean(y|~sig) remuestreando días."""
    df = pd.DataFrame({"y": y, "s": sig, "d": days}).dropna()
    if df.empty:
        return np.nan, np.nan
    g = df.groupby(["d", "s"]).y.agg(["sum", "size"]).unstack(fill_value=0)
    if ("sum", True) not in g.columns or ("sum", False) not in g.columns:
        return np.nan, np.nan
    s1, n1, s0, n0 = (g[(k, b)].to_numpy(float) for k, b in (("sum", True), ("size", True), ("sum", False), ("size", False)))
    idx = rng.integers(0, len(g), (B, len(g)))
    r = s1[idx].sum(1) / np.maximum(n1[idx].sum(1), 1) - s0[idx].sum(1) / np.maximum(n0[idx].sum(1), 1)
    return np.percentile(r, 2.5), np.percentile(r, 97.5)


def vote(y, sig, days):
    """un voto por día: media de las medias diarias con señal frente a sin señal."""
    df = pd.DataFrame({"y": y, "s": sig, "d": days}).dropna()
    m = df.groupby(["d", "s"]).y.mean().unstack()
    return m.get(True, pd.Series(dtype=float)).mean() - m.get(False, pd.Series(dtype=float)).mean()


def report(G, U, ev, title, sigs, rng):
    E = G[G[ev]]
    print(f"\n==== {title}: evento {ev}, n={len(E)}, tickers {E.tk.nunique()}, días {E.index.nunique()}")
    sc = lambda y: 1 if y == "r20" else 100
    dm = U.reindex(E.index)
    base = {y: E[y].mean() * sc(y) for y in Y_COLS}
    print("  base evento:   " + "  ".join(f"{y} {base[y]:5.1f}" for y in Y_COLS))
    print("  todos el mismo día: " + "  ".join(f"{y} {dm[y].mean()*sc(y):5.1f}" for y in Y_COLS))
    print("  dif. evento - mismo día (IS / OOS): " + "  ".join(
        f"{y} {((E[y]-dm[y])[E.index < SPLIT]).mean()*sc(y):+4.1f}/{((E[y]-dm[y])[E.index >= SPLIT]).mean()*sc(y):+4.1f}" for y in Y_COLS))
    rows = []
    days = E.index.to_numpy()
    is_ = days < SPLIT
    for s in sigs:
        sg = E[s].to_numpy(bool)
        if sg.sum() < 60 or (~sg).sum() < 60:
            continue
        r = {"señal": s, "n": int(sg.sum()), "pct": 100 * sg.mean()}
        for y in Y_COLS:
            yy = E[y].to_numpy(float)
            r[y] = np.nanmean(yy[sg]) * sc(y)
            r[y + "_d"] = (np.nanmean(yy[sg]) - np.nanmean(yy[~sg])) * sc(y)
            r[y + "_is"] = (np.nanmean(yy[sg & is_]) - np.nanmean(yy[~sg & is_])) * sc(y) if (sg & is_).sum() >= 30 else np.nan
            r[y + "_oos"] = (np.nanmean(yy[sg & ~is_]) - np.nanmean(yy[~sg & ~is_])) * sc(y) if (sg & ~is_).sum() >= 30 else np.nan
        for y in ("win10", "a10", "a20", "h21"):
            lo, hi = boot_diff(E[y].to_numpy(float), sg, days, rng)
            r[y + "_ci"] = (lo * 100, hi * 100)
            r[y + "_v"] = vote(E[y].to_numpy(float), sg, days) * 100
        rows.append(r)
    for r in sorted(rows, key=lambda r: -r["a20_d"]):
        print(f"  {r['señal']:20s} n={r['n']:6d} ({r['pct']:4.1f} %)  "
              f"win10 {r['win10']:5.1f} ({r['win10_d']:+4.1f} [{r['win10_ci'][0]:+4.1f},{r['win10_ci'][1]:+4.1f}] IS {r['win10_is']:+4.1f} OOS {r['win10_oos']:+4.1f})  "
              f"a10 {r['a10']:5.1f} ({r['a10_d']:+4.1f} v{r['a10_v']:+4.1f})  "
              f"a20 {r['a20']:5.1f} ({r['a20_d']:+4.1f} [{r['a20_ci'][0]:+4.1f},{r['a20_ci'][1]:+4.1f}] IS {r['a20_is']:+4.1f} OOS {r['a20_oos']:+4.1f} v{r['a20_v']:+4.1f})  "
              f"r20 {r['r20']:+5.2f} ({r['r20_d']:+4.2f})  h21 {r['h21']:5.1f} ({r['h21_d']:+4.1f} [{r['h21_ci'][0]:+4.1f},{r['h21_ci'][1]:+4.1f}])")
    return rows


def combos(G, ev, pairs, title):
    E = G[G[ev]]
    print(f"\n---- combinaciones ({title}, {ev})")
    is_ = E.index < SPLIT
    for nm, m in pairs:
        sg = m(E).to_numpy(bool)
        if sg.sum() < 60:
            print(f"  {nm:45s} n={sg.sum()} (insuficiente)")
            continue
        f = lambda y, mk: E[y][mk].mean() * (1 if y == "r20" else 100)
        print(f"  {nm:45s} n={sg.sum():6d}  win10 {f('win10', sg):5.1f} a10 {f('a10', sg):5.1f} a20 {f('a20', sg):5.1f} "
              f"(IS {f('a20', sg & is_):5.1f} / OOS {f('a20', sg & ~is_):5.1f})  r20 {f('r20', sg):+5.2f}  h21 {f('h21', sg):5.1f}"
              f"   | sin: a20 {f('a20', ~sg):5.1f} win10 {f('win10', ~sg):5.1f}")


PAIRS = [
    ("vol_rel_1,5 + cierre_alto_25", lambda E: E["vol_rel_1,5"] & E["cierre_alto_25"]),
    ("adx25 + medias_apiladas", lambda E: E["adx25"] & E["medias_apiladas"]),
    ("cerca_max52_5 + spy_sobre_sma200", lambda E: E["cerca_max52_5"] & E["spy_sobre_sma200"]),
    ("squeeze_previo + vol_dia_2", lambda E: E["squeeze_previo"] & E["vol_dia_2"]),
    ("rs_spy_63 + medias_apiladas + spy_sobre_sma200", lambda E: E["rs_spy_63"] & E["medias_apiladas"] & E["spy_sobre_sma200"]),
    ("gap_sin_cerrar + vol_dia_2", lambda E: E["gap_sin_cerrar"] & E["vol_dia_2"]),
    ("bajo_sma200 + macd_bajo_cero (rebote en bajista)", lambda E: E["bajo_sma200"] & E["macd_bajo_cero"]),
    ("extension_3atr + rsi_80 (agotamiento)", lambda E: E["extension_3atr"] & E["rsi_80"]),
]


def main():
    folder = Path(sys.argv[1])
    val = len(sys.argv) > 2 and sys.argv[2] == "val"
    yf = pd.read_pickle(folder / "yf.pkl")
    mk = market({k: yf[k] for k in ("SPY", "^VIX", "^VIX3M")})
    rng = np.random.default_rng(7)
    sets = [("27 tickers Yahoo 2004-2026", ld(yf))]
    if val:
        sets.append(("2.179 tickers (validación, supervivientes) 2004-2026", ld(pd.read_pickle(folder / "yf_val_ok.pkl"))))
    for title, data in sets:
        G, U = build(data, mk)
        sigs = [c for c in G.columns if c not in Y_COLS + ["imp", "brk", "tk"]]
        G.to_pickle(folder / f"CONT_{len(data)}.pkl")
        for ev in ("imp", "brk"):
            report(G, U, ev, title, sigs, rng)
            combos(G, ev, PAIRS, title)
        if len(data) < 100:
            etf = G[G.tk.isin(["SPY", "QQQ", "IWM", "SMH", "XLF", "GLD", "TLT"])]
            report(etf, U, "imp", title + " — solo ETF", sigs, rng)


if __name__ == "__main__":
    main()
