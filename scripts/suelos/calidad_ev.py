"""Ronda R16 (2026-11-09), paso 3: ¿los filtros de calidad mejoran la venta de puts? Sin tocar data/app.db.

Uso: .venv/Scripts/python scripts/suelos/calidad_ev.py CARPETA      # necesita CARPETA/Q.pkl de calidad_pit.py; escribe CARPETA/resultado.txt

Muestra: acciones de sectores NO exentos (la app no mide solvencia a financieras, energía, utilities, materiales ni inmobiliario), un cruce por ticker y
último día hábil de mes. Tres grupos por filtro: CUMPLE, NO CUMPLE y SIN DATO (valor no calculable en esa fecha): la comparación es solo entre los dos
primeros; el sin dato se cuenta aparte. Umbrales = los del estándar de `scanner.quality.thresholds` (`SolvencyThresholds()`).

PRUEBAS PRIMARIAS (fijadas antes de mirar): P1 «Caja (4 filtros)» (flujo op./deuda >= 0,30, capex/flujo <= 0,35, recompra neta >= 2 %, FCF/activos >= 0,12),
P2 «Deuda sana» (deuda/patrimonio <= 1, efectivo/deuda corta >= 1, cobertura >= 3) y P3 = P1 y P2 a la vez. Todo lo demás (cada filtro por separado) es
EXPLORATORIO: con ~15 filtros alguno saldrá «significativo» por azar.

Métrica principal: h21 (ningún cierre bajo entrada - 2 ATR en 21 sesiones, ya normalizada por volatilidad). Cola izquierda: % de entradas con peor mínimo a 21
sesiones <= -15 % y <= -20 %, y percentil 5 de ese mínimo. Informativas: h35, p10, ret21, ret126. Exceso = media del grupo menos la media de TODAS las filas
evaluables del mismo día (control 1) o del mismo día y tercil de volatilidad a 63 sesiones (control 2). IC95 % por bootstrap de FECHAS y por bootstrap de TICKERS
(los resultados de un mismo ticker se parecen entre meses, así que el IC por fechas por sí solo es optimista).

Sesgos que NO se pueden quitar: supervivencia (la lista es la de hoy: faltan las quebradas y deslistadas que la calidad debería evitar, lo que sesga hacia «sin
ventaja»), la prima no se mide (más seguro con menos IV no es mejor valor esperado), la BD solo tiene máximos y mínimos completos desde 2024, así que la muestra es 2024-2026 (~30 fechas, un único régimen alcista).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from scanner_opciones.config.settings import SolvencyThresholds        # noqa: E402

COMPANY_SECTORS = {"Consumer, Non-cyclical", "Industrial", "Technology", "Consumer, Cyclical", "Communications"}
T = SolvencyThresholds()
STD = {k: v["standard"] for k, v in T.model_dump().items()}
# (nombre, columna, mínimo/máximo, umbral estándar)
SINGLE = [
    ("Deuda/patrimonio ≤ 1", "debt_to_equity", "max", STD["debt_to_equity"]),
    ("Cobertura intereses ≥ 3", "interest_coverage", "min", STD["interest_coverage"]),
    ("Efectivo/deuda corta ≥ 1", "cash_to_short_debt", "min", STD["cash_to_short_debt"]),
    ("Flujo op./deuda ≥ 0,30", "ocf_to_debt", "min", STD["ocf_to_debt"]),
    ("Capex/flujo ≤ 0,35", "capex_to_ocf", "max", STD["capex_to_ocf"]),
    ("FCF/activos ≥ 0,08", "fcf_to_assets", "min", STD["fcf_to_assets"]),
    ("Recompra neta (acciones) ≥ 1 %", "net_buyback_pct", "min", STD["net_buyback_pct"]),
    ("ROIC ≥ 12 %", "roic", "min", STD["roic"]),
    ("Años con pérdidas ≤ 1", "loss_years", "max", STD["loss_years"]),
    ("Caídas de ingresos ≤ 1", "revenue_drop_years", "max", STD["revenue_drops"]),
    ("Volatilidad beneficios ≤ 0,5", "earnings_volatility", "max", STD["earnings_volatility"]),
    ("Ret. activos nuevos ≥ 0,20", "new_asset_return", "min", STD["new_asset_return"]),
    ("[aprox.] Recompras/cap. ≥ 1 %", "buyback_to_mcap_pct", "min", STD["buyback_to_mcap_pct"]),
    ("[aprox.] PEG histórico ≤ 1,6", "peg", "max", STD["peg"]),
]
HALVES = (("2024..2025-06", "2023-01-01", "2025-06-30"), ("2025-07..2026-09", "2025-07-01", "2026-12-31"))
rng = np.random.default_rng(16)


def state(df: pd.DataFrame, col: str, kind: str, lim: float) -> pd.Series:
    v = df[col]
    ok = v <= lim if kind == "max" else v >= lim
    return pd.Series(np.where(v.isna(), "nodata", np.where(ok, "pass", "fail")), index=df.index)


def combo(df: pd.DataFrame, parts) -> pd.Series:
    st = pd.concat([state(df, c, k, l) for c, k, l in parts], axis=1)
    return pd.Series(np.where((st == "nodata").any(axis=1), "nodata", np.where((st == "pass").all(axis=1), "pass", "fail")), index=df.index)


def boot_ratio(num: pd.Series, den: pd.Series, key: pd.Series, B: int = 2000):
    """IC95 % de sum(num)/sum(den) remuestreando los valores distintos de `key` (fechas o tickers)."""
    g = pd.DataFrame({"n": num, "d": den, "k": key}).groupby("k").sum()
    n, d = g["n"].to_numpy(), g["d"].to_numpy()
    idx = rng.integers(0, len(g), (B, len(g)))
    r = n[idx].sum(1) / d[idx].sum(1)
    return np.percentile(r, 2.5), np.percentile(r, 97.5)


def metric_cols(E: pd.DataFrame) -> pd.DataFrame:
    E = E.copy()
    E["t15"] = np.where(E.mae21.isna(), np.nan, (E.mae21 <= -15).astype(float) * 100)
    E["t20"] = np.where(E.mae21.isna(), np.nan, (E.mae21 <= -20).astype(float) * 100)
    for k in ("h21", "h35", "p10"):
        E[k] = E[k] * 100
    return E


MET = [("h21", "h21 %"), ("h35", "h35 %"), ("p10", "p10 %"), ("t15", "mín.≤-15 %"), ("t20", "mín.≤-20 %"), ("mae21", "mín. 21 s (%)"), ("ret21", "ret21 %"), ("ret126", "ret126 %")]


def evaluate(name: str, S: pd.Series, Q: pd.DataFrame, out: list[str], detail: bool) -> None:
    n_all = len(Q)
    cnt = S.value_counts()
    E = metric_cols(Q[S != "nodata"].assign(st=S[S != "nodata"]))
    if E.empty or (E.st == "pass").sum() < 100 or (E.st == "fail").sum() < 100:
        out.append(f"{name:<34} pass={cnt.get('pass', 0)} fail={cnt.get('fail', 0)} nodata={cnt.get('nodata', 0)}  (muestra insuficiente)")
        return
    E["vt"] = E.groupby("day").vol63.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False) if s.notna().sum() >= 6 else np.nan)
    line = [f"{name:<34} pass={cnt.get('pass', 0):>6} fail={cnt.get('fail', 0):>6} nodata={100 * cnt.get('nodata', 0) / n_all:4.0f}%"]
    P, F = E[E.st == "pass"], E[E.st == "fail"]
    for m, label in MET:
        line.append(f"{label} {P[m].mean():6.2f}/{F[m].mean():6.2f}")
    out.append(" | ".join(line))
    if not detail:
        return
    out.append(f"    p5 de mín. 21 s: cumple {P.mae21.quantile(0.05):.1f} %  no cumple {F.mae21.quantile(0.05):.1f} %   (tickers: cumple {P.tk.nunique()}, no cumple {F.tk.nunique()})")
    for m, label in (("h21", "h21"), ("t15", "mín.≤-15 %"), ("mae21", "mín. 21 s"), ("ret21", "ret21")):
        e1 = E[m] - E.groupby("day")[m].transform("mean")
        e2 = E[m] - E.groupby(["day", "vt"])[m].transform("mean")
        for tag, e in (("mismo día", e1), ("mismo día y tercil de vol.", e2)):
            ee = e[E.st == "pass"].dropna()
            if ee.empty:
                continue
            one = pd.Series(1.0, index=ee.index)
            kd, kt = E.loc[ee.index, "day"], E.loc[ee.index, "tk"]
            d_lo, d_hi = boot_ratio(ee, one, kd)
            t_lo, t_hi = boot_ratio(ee, one, kt)
            out.append(f"    exceso {label:<11} {tag:<27} {ee.mean():+7.2f}  IC fechas [{d_lo:+.2f}, {d_hi:+.2f}]  IC tickers [{t_lo:+.2f}, {t_hi:+.2f}]")
    for hname, a, b in HALVES:
        H = E[(E.day >= a) & (E.day <= b)]
        if (H.st == "pass").sum() < 50:
            continue
        hp = H[H.st == "pass"]
        ex = (hp.h21 - H.groupby("day").h21.transform("mean").loc[hp.index]).mean()
        ex15 = (hp.t15 - H.groupby("day").t15.transform("mean").loc[hp.index]).mean()
        out.append(f"    mitad {hname}: n={len(hp)}  exceso h21 {ex:+.2f}  exceso mín.≤-15 % {ex15:+.2f}")


def main():
    folder = Path(sys.argv[1])
    Q = pd.read_pickle(folder / "Q.pkl")
    Q = Q[Q.sector.isin(COMPANY_SECTORS)].copy()
    Q = Q.dropna(subset=["h21"])               # sin resultado a 21 sesiones no hay medida
    out = [f"Muestra: {len(Q)} filas, {Q.tk.nunique()} tickers, {Q.day.nunique()} fechas ({Q.day.min().date()} .. {Q.day.max().date()}); sectores no exentos",
           f"Sin hechos de EDGAR en la fecha (nodata): {100 * Q.nodata.mean():.0f} % de las filas",
           "Formato: cumple / no cumple. h21, h35, p10: % de entradas sin tocar (mayor = mejor). mín.≤-15/-20 %: % de entradas con peor mínimo a 21 sesiones por debajo de ese umbral (menor = mejor).", ""]
    P1 = [("ocf_to_debt", "min", 0.30), ("capex_to_ocf", "max", 0.35), ("net_buyback_pct", "min", 2.0), ("fcf_to_assets", "min", 0.12)]
    P2 = [("debt_to_equity", "max", 1.0), ("cash_to_short_debt", "min", 1.0), ("interest_coverage", "min", 3.0)]
    out.append("=== PRIMARIAS ===")
    for name, parts in (("P1 Caja (4 filtros)", P1), ("P2 Deuda sana", P2), ("P3 = P1 y P2", P1 + P2)):
        evaluate(name, combo(Q, parts), Q, out, True)
    out += ["", "=== EXPLORATORIAS (cada filtro estándar por separado) ==="]
    for name, col, kind, lim in SINGLE:
        evaluate(name, state(Q, col, kind, lim), Q, out, False)
    out += ["", "=== Todas las filas con dato en P1 o P2: base (media de todas) ==="]
    allrows = metric_cols(Q)
    out.append(" | ".join(f"{label} {allrows[m].mean():.2f}" for m, label in MET))
    text = "\n".join(out)
    (folder / "resultado.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
