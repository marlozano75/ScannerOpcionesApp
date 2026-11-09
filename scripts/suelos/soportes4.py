"""Valor del soporte según su DISTANCIA al precio y su ANTIGÜEDAD (2026-11-09). Solo lectura de data/app.db.

Uso: .venv/Scripts/python scripts/suelos/soportes4.py [max_tickers]

Para cada ticker y origen (6 de 63 sesiones) toma TODOS los soportes validados (>= 3 pivotes, no perdidos) bajo el precio, con datos hasta
el origen. Mide en las 63 sesiones siguientes: (a) si el precio llega a la zona (relevancia), (b) rebote a 3 sesiones y (c) «aguanta 21
sesiones» (ningún cierre bajo la zona menos la banda) en la primera aproximación. Se agrupa por distancia al precio y por antigüedad del
último pivote. CONTROL: la misma zona desplazada un 4 % hacia abajo (sin soporte propio: no solapa ningún grupo de mínimos), para ver si el
soporte aporta algo frente a un nivel cualquiera a esa distancia.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import medias_soportes as ms
from scanner_opciones.config.settings import SupportLevelSettings
from scanner_opciones.metrics.support_levels import support_levels

SCFG = SupportLevelSettings()
BAND = 1.5
DIST = [0, 3, 6, 10, 15, 20, 30, 100]
AGE = [0, 90, 180, 365, 730, 5000]


def first_event(l, c, lo, hi, cut):
    n = len(c)
    near = l <= hi * 1.003
    for t in range(max(cut, 1), min(cut + ms.TEST, n - 21)):
        if near[t] and not near[t - 1] and c[t - 1] > hi:
            return (bool(c[t] >= lo and c[t + 3] > lo * (1 - BAND / 200)), bool((c[t:t + 22] >= lo * (1 - BAND / 100)).all()))
    return None


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    data = ms.load(Path("data/app.db"), limit)
    print(len(data), "tickers", flush=True)
    rows = []
    for t, g in data.items():
        days = [d.date() if hasattr(d, "date") else pd.Timestamp(d).date() for d in g.day]
        h, l, c, o_ = (g[k].to_numpy(float) for k in ("high", "low", "close", "open"))
        n = len(c)
        candles = list(zip(days, o_, h, l, c))
        for o in range(1, ms.ORIGINS + 1):
            cut = n - o * ms.TEST
            if cut - ms.TRAIN < 60:
                break
            price, today = c[cut - 1], days[cut - 1]
            lv_all = support_levels(candles[:cut], SCFG)
            for lv in lv_all:
                if lv.pivots < SCFG.min_pivots or lv.high >= price:
                    continue
                dist = (price / lv.high - 1) * 100
                age = (today - lv.last_pivot).days
                ev = first_event(l, c, lv.low, lv.high, cut)
                reached = ev is not None
                rows.append(("sop", t, o, dist, age, lv.pivots, reached, ev[0] if ev else None, ev[1] if ev else None))
                # control: zona desplazada 4 % abajo, sin solapar ningún grupo de mínimos (de cualquier nº de pivotes)
                clo, chi = lv.low * 0.96, lv.high * 0.96
                if not any(x.low <= chi * 1.015 and x.high >= clo * 0.985 for x in lv_all):
                    ev = first_event(l, c, clo, chi, cut)
                    rows.append(("ctl", t, o, (price / chi - 1) * 100, age, 0, ev is not None, ev[0] if ev else None, ev[1] if ev else None))
    d = pd.DataFrame(rows, columns=["k", "t", "o", "dist", "age", "piv", "reached", "reb", "hold"])
    d["db"] = pd.cut(d.dist, DIST, right=False)
    d["ab"] = pd.cut(d.age, AGE, right=False)

    def tab(x, by, title):
        print(f"\n== {title} ==")
        for key, s in x.groupby(by, observed=True):
            e = s[s.reached]
            if len(e) == 0:
                print(f"{str(key):16s} zonas {len(s):6d}  llega {0:5.1%}"); continue
            print(f"{str(key):16s} zonas {len(s):6d} | llega en 63 ses. {s.reached.mean():5.1%} | aprox. {len(e):5d} | "
                  f"rebote {e.reb.astype(bool).mean():5.1%} | aguanta21 {e.hold.astype(bool).mean():5.1%}")

    s, k = d[d.k == "sop"], d[d.k == "ctl"]
    tab(s, "db", "SOPORTES (>=3 pivotes) por distancia al precio (%)")
    tab(k, "db", "CONTROL (misma zona 4 % más abajo, sin soporte) por distancia")
    tab(s, "ab", "SOPORTES por antigüedad del último pivote (días)")
    tab(s[s.dist.between(3, 15)], "ab", "SOPORTES a 3-15 % por antigüedad (distancia controlada)")
    tab(k[k.dist.between(3, 15)], "ab", "CONTROL a 3-15 % por antigüedad de la zona original")
    tab(s[s.dist.between(3, 15)], "piv", "SOPORTES a 3-15 % por nº de pivotes")


if __name__ == "__main__":
    main()
