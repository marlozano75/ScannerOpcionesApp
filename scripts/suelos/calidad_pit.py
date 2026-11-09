"""Ronda R16 (2026-11-09), paso 2: métricas de calidad CONOCIDAS EN CADA FECHA + resultado posterior de vender una put. Sin tocar data/app.db (solo lectura).

Uso: .venv/Scripts/python scripts/suelos/calidad_pit.py CARPETA     # necesita CARPETA/facts.pkl de calidad_fetch.py; escribe CARPETA/Q.pkl

Punto en el tiempo: para cada ticker y cada último día hábil de mes (2023-03 .. 2026-09; las que no tienen resultado posterior, por falta de máximos/mínimos en la BD antes de 2024, se descartan después) se recortan los hechos de EDGAR a `filed <= fecha` y se pasan por
el MISMO analizador de la app (`parse_company_facts(..., today=fecha)`: así `STALE_DAYS` se mide frente a esa fecha y no frente a hoy). Nunca se lee
`ticker_quality`: son los valores de hoy (mirar al futuro).

Capitalización estimada = cierre del día × acciones de `dei:EntityCommonStockSharesOutstanding` presentadas hasta esa fecha: con varias clases de acciones
suma solo una, así que PEG histórico y recompras/capitalización son APROXIMADOS. El PEG de analistas no tiene histórico y no se prueba.

Resultados (entrada al cierre de la fecha; mismas definiciones de medias_reglas.py): h21/h35 = ningún cierre bajo entrada - 2 ATR en 21/35 sesiones; mae21 =
peor mínimo a 21 sesiones frente a la entrada (%); p10 = ningún cierre bajo -10 % en 21 sesiones; ret21/ret126 = retorno a 21/126 sesiones (%). vol63 = volatilidad
anual a 63 sesiones (control).
"""
from __future__ import annotations

import bisect
import dataclasses
import pickle
import sqlite3
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from medias_reglas import feats, load_db, market        # noqa: E402
from scanner_opciones.domain.models import TickerInfo   # noqa: E402
from scanner_opciones.marketdata.edgar import parse_company_facts   # noqa: E402
from scanner_opciones.marketdata.financials import Financials       # noqa: E402

FIRST, LAST = pd.Timestamp("2023-03-01"), pd.Timestamp("2026-09-30")
INFO_FIELDS = {f.name for f in dataclasses.fields(TickerInfo)}
FIN_FIELDS = [f.name for f in dataclasses.fields(Financials) if f.name in INFO_FIELDS]


def asof_days(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(index, index=index)
    return [d for d in s.groupby([index.year, index.month]).last().tolist() if FIRST <= d <= LAST]


def sorted_tree(tree: dict) -> dict:
    """Cada lista de hechos ordenada por fecha de presentación (para recortar con bisect)."""
    out = {}
    for tax, tags in tree.items():
        out[tax] = {tag: {"units": {u: sorted(fs, key=lambda f: f.get("filed", "")) for u, fs in body["units"].items()}} for tag, body in tags.items()}
    return out


def cut(tree: dict, day: str) -> dict:
    """Hechos presentados hasta `day` (incluido)."""
    out = {}
    for tax, tags in tree.items():
        t2 = {}
        for tag, body in tags.items():
            units = {}
            for u, fs in body["units"].items():
                k = bisect.bisect_right([f.get("filed", "") for f in fs], day)
                if k:
                    units[u] = fs[:k]
            if units:
                t2[tag] = {"units": units}
        out[tax] = t2
    return out


def work(args):
    ticker, tree, closes = args                      # closes: {fecha ISO: cierre}
    tree = sorted_tree(tree)
    rows = []
    for day, close in closes.items():
        pit = cut(tree, day)
        fin = parse_company_facts({"facts": {"us-gaap": pit["us-gaap"]}}, pd.Timestamp(day).date())
        shares = pit.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
        mcap = close * shares[-1]["val"] if shares else None
        row = {"tk": ticker, "day": pd.Timestamp(day), "mcap": mcap, "nodata": fin is None}
        if fin is not None:
            info = TickerInfo(ticker, market_cap=mcap, **{k: getattr(fin, k) for k in FIN_FIELDS})
            for k in FIN_FIELDS:
                row[k] = getattr(fin, k)
            row["peg"], row["buyback_to_mcap_pct"] = info.peg, info.buyback_to_mcap_pct
        rows.append(row)
    return rows


def outcomes(data: dict, mk) -> pd.DataFrame:
    parts = []
    for t, g in data.items():
        if t == "SPY":
            continue
        F = feats(t, g, mk)[["c", "a", "h21", "h35", "mae21", "mc21"]].copy()
        c = g["close"].to_numpy(float)
        n = len(c)
        for k in (21, 126):
            F[f"ret{k}"] = pd.Series(np.r_[(c[k:] / c[:-k] - 1) * 100, np.full(k, np.nan)], index=g.index).reindex(F.index)
        F["p10"] = np.where(F.mc21.isna(), np.nan, (F.mc21 >= 0.9 * F.c).astype(float))
        F["vol63"] = (np.log(g["close"]).diff().rolling(63).std() * np.sqrt(252) * 100).reindex(F.index)
        F["tk"] = t
        parts.append(F)
    return pd.concat(parts)


def main():
    folder = Path(sys.argv[1])
    with open(folder / "facts.pkl", "rb") as fh:
        facts = pickle.load(fh)
    data = load_db()
    if "SPY" not in data:
        sys.exit("falta SPY")
    mk = market({"SPY": data["SPY"]})
    days = asof_days(data["SPY"].index)
    print(f"{len(facts)} tickers con hechos, {len(days)} fechas ({days[0].date()} .. {days[-1].date()})", flush=True)

    jobs = []
    for t, tree in facts.items():
        if t not in data:
            continue
        idx = data[t].index
        closes = {d.strftime("%Y-%m-%d"): float(data[t].close.loc[d]) for d in days if d in idx and idx.get_loc(d) >= 60}
        if closes:
            jobs.append((t, tree, closes))
    print(f"{len(jobs)} tickers a evaluar", flush=True)
    rows = []
    with Pool() as pool:
        for i, r in enumerate(pool.imap_unordered(work, jobs, chunksize=4), 1):
            rows.extend(r)
            if i % 200 == 0:
                print(f"  {i} tickers", flush=True)
    Q = pd.DataFrame(rows)

    O = outcomes(data, mk).reset_index().rename(columns={"index": "day"})
    O["day"] = pd.to_datetime(O["day"])
    Q = Q.merge(O, on=["tk", "day"], how="left")
    con = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
    sec = pd.read_sql_query("SELECT ticker AS tk, sector FROM ticker_info", con)
    Q = Q.merge(sec, on="tk", how="left")
    Q.to_pickle(folder / "Q.pkl")
    print(f"listo: {len(Q)} filas, {Q.tk.nunique()} tickers -> {folder / 'Q.pkl'}", flush=True)


if __name__ == "__main__":
    main()
