"""Limpieza de cierres diarios con valores erróneos alrededor de un split.

Medido con los datos de tastytrade (2026-11-02): el cierre del día del split llega sin ajustar (NFLX 1112,6 en vez de 111; CMG 3283 en vez de 65;
NVDA 1208 en vez de 121; NVDL 29,6 y 86,3 en un split inverso) y un split inverso deja sin ajustar todo lo anterior (NVDL ~10 antes, ~28 después).
Un pico así encoge la escala vertical de los gráficos y falsea medias y ATR, así que al cargar los cierres:
1. se descarta el cierre que cae fuera de su propio rango del día (máximo/mínimo) o es un pico aislado respecto a sus vecinos;
2. si queda un salto de nivel que coincide con una razón de split (2, 3, 4, 5, 10…), se reescala lo anterior.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

SPIKE_RATIO = 1.8             # un cierre a más de esta razón (arriba o abajo) de sus dos vecinos, en el mismo sentido, es un pico aislado
RANGE_SLACK = 0.15            # margen sobre el máximo/mínimo del día para dar por válido el cierre
SPLIT_RATIOS = (2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50, 100)
SPLIT_TOLERANCE = 0.12        # lo que puede alejarse el salto observado de la razón exacta (incluye el movimiento real del día)
# Entre estos dos límites el salto de un día a otro no puede coincidir con ninguna razón de split (ni directa ni inversa): es el 99,9 % de los
# días, y probar las 28 razones en cada uno era casi todo el coste de limpiar los 2 millones de velas (8,4 s → 1,5 s, mismo resultado; 2026-11-12)
NO_SPLIT_BELOW = min(SPLIT_RATIOS) * (1 - SPLIT_TOLERANCE)
NO_SPLIT_ABOVE = (1 + SPLIT_TOLERANCE) / min(SPLIT_RATIOS)

Row = tuple[date, float, Optional[float], Optional[float]]     # día, cierre, máximo, mínimo


def _outside_range(close: float, high: Optional[float], low: Optional[float]) -> bool:
    return (high is not None and high > 0 and close > high * (1 + RANGE_SLACK)) or (low is not None and low > 0 and close < low * (1 - RANGE_SLACK))


def appends_cleanly(last_close: float, close: float) -> bool:
    """True si añadir un cierre `close` (sin máximo ni mínimo, como el provisional del día) tras unos cierres ya limpios cuyo último es
    `last_close` deja lo anterior intacto: `clean_bars(filas + [nueva]) == clean_bars(filas) + [nueva]`.

    Por qué basta con mirar la razón: el último cierre limpio es siempre el último que pasó el filtro de rango (el de picos no puede quitar
    el último, no tiene vecino siguiente, y el reescalado por split nunca toca el último). Con la fila nueva ese cierre pasa a tener vecino
    siguiente, y solo sería un pico si se alejara de él más de `SPIKE_RATIO` (1,8×); y la fila nueva solo provoca un reescalado si su salto
    cae en una razón de split. Dentro de (`NO_SPLIT_ABOVE`, `NO_SPLIT_BELOW`) = (0,56; 1,76) no pasa ninguna de las dos cosas."""
    return close > 0 and last_close > 0 and NO_SPLIT_ABOVE < close / last_close < NO_SPLIT_BELOW


def clean_bars(rows: Sequence[Row]) -> list[tuple[date, float]]:
    """Cierres (día, cierre) ascendentes ya limpios; `rows` debe venir ordenado por día."""
    kept = [(d, c) for d, c, h, lo in rows if c > 0 and not _outside_range(c, h, lo)]
    # picos aislados (también donde no hay máximo/mínimo guardado)
    out: list[tuple[date, float]] = []
    for i, (d, c) in enumerate(kept):
        prev = out[-1][1] if out else None
        nxt = kept[i + 1][1] if i + 1 < len(kept) else None
        if prev and nxt and ((c > prev * SPIKE_RATIO and c > nxt * SPIKE_RATIO) or (c < prev / SPIKE_RATIO and c < nxt / SPIKE_RATIO)):
            continue
        out.append((d, c))
    # saltos de nivel por un split que el proveedor no ajustó atrás
    for i in range(len(out) - 1, 0, -1):
        ratio = out[i][1] / out[i - 1][1]
        if NO_SPLIT_ABOVE < ratio < NO_SPLIT_BELOW:
            continue
        for k in SPLIT_RATIOS:
            for factor in (k, 1 / k):
                if abs(ratio / factor - 1) <= SPLIT_TOLERANCE:
                    out[:i] = [(d, c * ratio) for d, c in out[:i]]
                    break
            else:
                continue
            break
    return out
