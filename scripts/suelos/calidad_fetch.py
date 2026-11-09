"""Ronda R16 (2026-11-09), paso 1: hechos de SEC EDGAR para probar los filtros de calidad en el pasado. No toca data/app.db (solo lectura).

Uso: .venv/Scripts/python scripts/suelos/calidad_fetch.py CARPETA

Descarga UNA vez el volcado completo de la SEC (`companyfacts.zip`, ~1,4 GB: más amable con el límite de peticiones que 2.300 consultas) y la lista
ticker -> CIK, y deja en CARPETA/facts.pkl los hechos de cada ticker de la BD recortados a las etiquetas que lee `edgar.py` (más las acciones en
circulación para estimar la capitalización). Cada hecho conserva `start, end, val, form, filed`: `filed` permite reconstruir lo que se SABÍA en cada fecha.
Lee `edgar.contact` de config/config.yaml para el User-Agent (la SEC lo exige); no se imprime.
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import unicodedata
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from scanner_opciones.config.settings import load_settings          # noqa: E402
from scanner_opciones.marketdata.edgar import TAXONOMIES             # noqa: E402

ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EXTRA_US_GAAP = ("WeightedAverageNumberOfDilutedSharesOutstanding",)
KEEP = ("start", "end", "val", "form", "filed")


def wanted_tags() -> set[str]:
    tags = set(EXTRA_US_GAAP)
    for value in TAXONOMIES["us-gaap"].values():
        tags.update(value)
    return tags


def prune(doc: dict, tags: set[str]) -> dict:
    out = {"us-gaap": {}, "dei": {}}
    for tag, body in doc.get("facts", {}).get("us-gaap", {}).items():
        if tag in tags:
            out["us-gaap"][tag] = {"units": {u: [{k: f[k] for k in KEEP if k in f} for f in facts]
                                             for u, facts in body.get("units", {}).items() if u in ("USD", "shares")}}
    for tag in ("EntityCommonStockSharesOutstanding",):
        body = doc.get("facts", {}).get("dei", {}).get(tag)
        if body:
            out["dei"][tag] = {"units": {"shares": [{k: f[k] for k in KEEP if k in f} for f in body.get("units", {}).get("shares", [])]}}
    return out


def main() -> None:
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    contact = load_settings("config/config.yaml").edgar.contact
    ascii_contact = unicodedata.normalize("NFKD", contact).encode("ascii", "ignore").decode().strip()
    headers = {"User-Agent": f"ScannerOpcionesApp {ascii_contact}"}

    zpath = folder / "companyfacts.zip"
    if not zpath.exists() or zpath.stat().st_size < 1_000_000_000:
        print("descargando companyfacts.zip ...", flush=True)
        with httpx.stream("GET", ZIP_URL, headers=headers, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            done = 0
            with open(zpath, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if done % (100 << 20) < (1 << 20):
                        print(f"  {done >> 20} MB", flush=True)
    ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in httpx.get(TICKERS_URL, headers=headers, timeout=60).json().values()}

    con = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
    tickers = [r[0] for r in con.execute("SELECT ticker FROM daily_bars GROUP BY ticker HAVING COUNT(*) >= 330")]
    by_cik: dict[int, list[str]] = {}
    for t in tickers:
        c = ciks.get(t.upper().replace(".", "-")) or ciks.get(t.upper())
        if c:
            by_cik.setdefault(c, []).append(t)
    print(f"{len(tickers)} tickers con velas; {sum(len(v) for v in by_cik.values())} con CIK", flush=True)

    tags, out, n = wanted_tags(), {}, 0
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        for cik, ts in by_cik.items():
            name = f"CIK{cik:010d}.json"
            if name not in names:
                continue
            doc = json.loads(z.read(name))
            pruned = prune(doc, tags)
            if not pruned["us-gaap"]:
                continue                          # emisor extranjero (IFRS): sin us-gaap, se queda fuera de la prueba
            for t in ts:
                out[t] = pruned
            n += 1
            if n % 200 == 0:
                print(f"  {n} empresas", flush=True)
    with open(folder / "facts.pkl", "wb") as fh:
        pickle.dump(out, fh)
    print(f"listo: {len(out)} tickers ({n} empresas) con hechos us-gaap -> {folder / 'facts.pkl'}", flush=True)


if __name__ == "__main__":
    main()
