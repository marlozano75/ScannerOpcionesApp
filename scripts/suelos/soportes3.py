"""Comparación del filtro de soporte actual (cierres, `ta.support_zones`) con los soportes de mínimos (`metrics/support_levels.py`).

Uso: .venv/Scripts/python scripts/suelos/soportes3.py [max_tickers]

En 6 orígenes de 63 sesiones y para cada ticker, cada método da su mejor soporte bajo el precio (usando solo datos hasta el origen). Se mide en
las 63 sesiones siguientes la PRIMERA aproximación del precio a esa zona: rebote a 3 sesiones y «aguanta 21 sesiones» (ningún cierre bajo la
zona menos la banda). Variantes: A = actual (365 días, cierres); B = actual con 1400 días; C = nuevo (mínimos, 1400 días, >= 3 pivotes);
D = nuevo con 365 días. También cobertura y distancia al precio.
"""
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import medias_soportes as ms
from scanner_opciones.config.settings import SupportLevelSettings, TechnicalSettings
from scanner_opciones.metrics import technical as ta
from scanner_opciones.metrics.support_levels import nearest_support, support_levels

TCFG, SCFG = TechnicalSettings(), SupportLevelSettings()
BAND = 1.5


def old_zone(days, closes, price, today, lookback):
    bars = list(zip(days, closes))
    zs = ta.support_zones(bars, today, lookback, TCFG.support_band_pct, TCFG.support_min_touches, TCFG.support_min_clusters,
                          TCFG.support_pivot_width, TCFG.support_cluster_gap_days)
    z = ta.best_support(zs, price)
    return (z.low, z.high) if z else None


def new_zone(candles, price):
    z = nearest_support(support_levels(candles, SCFG), price, SCFG.min_pivots)
    return (z.low, z.high) if z else None


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
        h, l, c = g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float)
        o_ = g.open.to_numpy(float)
        n = len(c)
        candles = list(zip(days, o_, h, l, c))
        for o in range(1, ms.ORIGINS + 1):
            cut = n - o * ms.TEST
            if cut - ms.TRAIN < 60:
                break
            price, today = c[cut - 1], days[cut - 1]
            cs = candles[:cut]
            c365 = [x for x in cs if (today - x[0]).days <= 365]
            zones = {"A actual 365d": old_zone(days[:cut], c[:cut], price, today, 365),
                     "B actual 1400d": old_zone(days[:cut], c[:cut], price, today, 1400),
                     "C nuevo 1400d": new_zone(cs, price),
                     "D nuevo 365d": new_zone(c365, price) if len(c365) > 12 else None}
            for k, z in zones.items():
                ev = first_event(l, c, z[0], z[1], cut) if z else None
                rows.append((k, t, o, z is not None, (price / z[1] - 1) * 100 if z else np.nan, None if ev is None else ev[0], None if ev is None else ev[1]))
    d = pd.DataFrame(rows, columns=["m", "t", "o", "has", "dist", "reb", "hold"])
    out = []
    for k, x in d.groupby("m"):
        e = x[x.reb.notna()]
        out.append(f"{k:15s} cobertura {x.has.mean():5.1%} | distancia mediana {x.dist.median():5.1f}% | aproximaciones {len(e):5d} "
                   f"({len(e) / max(x.has.sum(), 1):.0%} de las zonas) | rebote {e.reb.astype(bool).mean():.1%} | aguanta21 {e.hold.astype(bool).mean():.1%}")
    # a igualdad de distancia: solo zonas a 3-10 % del precio
    out.append("\nSolo zonas a 3-10 % bajo el precio (comparable entre métodos):")
    for k, x in d.groupby("m"):
        e = x[x.reb.notna() & x.dist.between(3, 10)]
        out.append(f"{k:15s} aproximaciones {len(e):5d} | rebote {e.reb.astype(bool).mean():.1%} | aguanta21 {e.hold.astype(bool).mean():.1%}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
