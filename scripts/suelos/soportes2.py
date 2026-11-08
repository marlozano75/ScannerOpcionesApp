"""Soportes horizontales, afinado de la banda (2026-11-08). Solo lectura de data/app.db.

Uso: .venv/Scripts/python scripts/suelos/soportes2.py [max_tickers]

Para cada banda de agrupación (0,5/1/1,5/2/3 %) construye los soportes con datos hasta T (6 orígenes de 63 sesiones) y mide, en las
63 sesiones siguientes, por pivotes previos del nivel: tasa de rebote a 3 sesiones y tasa de «aguanta 21 sesiones» (ningún cierre
por debajo del nivel menos la banda), que es lo que le importa a un vendedor de puts.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import medias_soportes as ms

limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
data = ms.load(Path("data/app.db"), limit)
print(len(data), "tickers", flush=True)
for band in (0.5, 1.0, 1.5, 2.0, 3.0):
    ms.BAND_PCT = band
    rows = []
    for t, g in data.items():
        h, l, c = g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float)
        n = len(c)
        for o in range(1, ms.ORIGINS + 1):
            cut = n - o * ms.TEST
            if cut - ms.TRAIN < 60:
                break
            for lo, hi, npiv, first, last in ms.build_levels(h, l, c, cut):
                if hi >= c[cut - 1]:
                    continue
                for tt, ok in ms.level_events(l, c, lo, hi, cut, cut + ms.TEST):
                    if tt + 21 < n:
                        rows.append((min(npiv, 4), ok, bool((c[tt:tt + 22] >= lo * (1 - band / 100)).all())))
    d = pd.DataFrame(rows, columns=["np", "reb", "hold21"])
    print(f"banda {band}%: n={len(d)} | " + " | ".join(
        f"{k}p rebote {x.reb.mean():.1%} aguanta21 {x.hold21.mean():.1%} ({len(x)})" for k, x in d.groupby("np")), flush=True)
