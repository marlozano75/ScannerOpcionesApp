"""R21: velas diarias de bid y ask (tastytrade, DXLink) de todas las patas del registro de escaneos (`scan_log`), desde el primer día del registro.

Uso (desde la raíz del repo): .venv/Scripts/python scripts/suelos/registro_fetch.py $D   -> $D/paths.json
Lee `data/app.db` en solo lectura y las credenciales de tastytrade de `config/config.yaml`. ~2.900 símbolos tardan unos minutos; tastytrade a veces
no responde a la primera conexión (se reintenta en 3 pasadas por lote). Si falla del todo, volver a lanzarlo.
"""
import asyncio, json, logging, sqlite3, sys
from datetime import date

sys.path.insert(0, "src")
from scanner_opciones.config.settings import load_settings
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, quiet_sdk_logging
from scanner_opciones.scanner import yield_history as yh

quiet_sdk_logging(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(message)s")
OUT = sys.argv[1] + "/paths.json"

db = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
symbols, start = set(), None
for t, e, k, lk, fs in db.execute("select ticker, expiry, strike, long_strike, first_seen from scan_log"):
    root = yh.dx_root(t)
    if not root:          # clases de acciones (BRK B…): no se piden
        continue
    d = date.fromisoformat(e)
    symbols.add(yh.option_symbol(root, d, k))
    if lk:
        symbols.add(yh.option_symbol(root, d, lk))
    start = min(start or fs[:10], fs[:10])
print(f"{len(symbols)} símbolos desde {start}", flush=True)

s = load_settings("config/config.yaml")
tv = TastytradeVolatility(s.tastytrade.client_secret, s.tastytrade.refresh_token)
days = (date.today() - date.fromisoformat(start)).days + 3


async def main() -> None:
    prog = lambda a, t, p: print(f"  {a}/{t} pasada {p}", flush=True)
    out = {}
    for price in ("ask", "bid"):
        got = await tv._sdk_fetch_option_series(sorted(symbols), days, price, prog)
        out[price] = {sym: {str(d): v for d, v in m.items()} for sym, m in got.items()}
        print(f"{price}: {len(got)}/{len(symbols)} con respuesta", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f)

asyncio.run(main())
