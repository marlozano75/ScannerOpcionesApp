"""R21: ¿cómo habría ido entrar en lo que sacó el escáner y gestionarlo con las salidas de cada perfil? ¿Qué criterios lo habrían mejorado?

Uso (desde la raíz del repo): .venv/Scripts/python scripts/suelos/registro_ev.py $D [mid|ask]   (necesita $D/paths.json de registro_fetch.py)

- Entradas: registro de escaneos (`scan_log`, solo lectura), UNA por perfil o fuente, ticker y vencimiento (la de mayor yield al aparecer), a 1 contrato
  y al precio de referencia del escáner al aparecer por primera vez (`first_price`; en spreads, el crédito neto).
- Valoración diaria con las velas de cierre de tastytrade: `mid` = (bid + ask) / 2 de cada pata; `ask` = recompra al ask y venta de la comprada al bid.
- Salidas de cada perfil: TPC (Mensual, Táctico) al vencimiento; TastyPut y las fuentes Comunidad/MarketRadar (perfil de gestión MTP, estilo tastytrade):
  50 % de la prima o 21 DTE; spreads, además, stop cuando el spread vale 2 × el crédito. Al vencer: valor intrínseco con el cierre del subyacente.
- Comisión 1,05 $ por pata al abrir y al cerrar (no al vencer). Capital: put = strike × 100; spread = ancho − crédito.
- Escribe $D/registro_<modo>.json (una fila por entrada) e imprime: resumen por perfil, resultado por característica de entrada y salidas alternativas.
"""
import json, sqlite3, sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "src")
from scanner_opciones.scanner import yield_history as yh

D = sys.argv[1]
MODE = sys.argv[2] if len(sys.argv) > 2 else "mid"
with open(D + "/paths.json", encoding="utf-8") as f:
    paths = json.load(f)
ASK = {s: {date.fromisoformat(d): v for d, v in m.items()} for s, m in paths["ask"].items()}
BID = {s: {date.fromisoformat(d): v for d, v in m.items()} for s, m in paths["bid"].items()}
FEE = 1.05
TPC_PROFILES = {"Mensual (TPC)", "Táctico (TPC)"}
db = sqlite3.connect("file:data/app.db?mode=ro", uri=True)
TODAY = max(max(m) for m in ASK.values() if m)     # último día con velas

closes = defaultdict(dict)
for t, d, c in db.execute("select ticker, day, close from daily_bars where day >= ?", (str(TODAY - timedelta(days=200)),)):
    closes[t][date.fromisoformat(d)] = c
earn = defaultdict(set)    # resultados ya publicados (histórico) y el próximo anunciado
for t, raw in db.execute("select ticker, earnings from company_history where earnings is not null"):
    for q in json.loads(raw):
        earn[t].add(date.fromisoformat(q["day"]))
for t, d in db.execute("select ticker, next_earnings from ticker_quality where next_earnings is not null"):
    earn[t].add(date.fromisoformat(d[:10]))


def before(t, d, n):
    s = closes.get(t, {})
    return [s[k] for k in sorted(k for k in s if k < d)][-n:]


def spot_at(t, d):
    s = closes.get(t, {})
    for b in range(4):
        if d - timedelta(days=b) in s:
            return s[d - timedelta(days=b)]
    return None


entries = {}
for origin, profile, kind, t, e, k, lk, fs, fp, fy, dl, ivr, dist, dte in db.execute(
        """select origin, profile, kind, ticker, expiry, strike, long_strike, first_seen, first_price, first_yield, delta, iv_rank, distance_pct, dte
           from scan_log order by first_seen, coalesce(first_yield, 0) desc"""):
    group = profile if origin == "scan" else origin     # las fuentes se anotan bajo varios perfiles: una entrada por fuente
    key = (group, kind, t, e)
    if key in entries or not fp or fp <= 0 or not yh.dx_root(t):
        continue
    d0, ed = date.fromisoformat(fs[:10]), date.fromisoformat(e)
    px, spy = before(t, d0, 50), before("SPY", d0, 20)
    entries[key] = dict(group=group, kind=kind, t=t, e=ed, k=k, lk=lk, d0=d0, credit=fp, yld=fy, delta=dl, ivr=ivr, dist=dist, dte=dte,
                        earn=any(d0 <= x <= ed for x in earn.get(t, ())),
                        above50=(px[-1] > mean(px)) if len(px) >= 50 else None,
                        ret5=(px[-1] / px[-6] - 1) * 100 if len(px) >= 6 else None,
                        spy_up=(spy[-1] > mean(spy)) if len(spy) >= 20 else None)


def leg(sym, d, short):
    a, b = ASK.get(sym, {}).get(d), BID.get(sym, {}).get(d)
    if MODE == "mid" and a is not None and b is not None and a >= b:
        return (a + b) / 2
    if short:
        return a if a is not None else (b or 0.0)
    return b if b is not None else 0.0


def run(x, tp=None, stop=None, review=None, touch=False):
    """tp: % de la prima ganado; stop: coste de recompra ≥ stop × crédito; review: DTE (solo si se entró con más); touch: el subyacente cierra bajo el strike.
    Devuelve (P&L $, estado, motivo) o None sin datos."""
    root = yh.dx_root(x["t"])
    ss = yh.option_symbol(root, x["e"], x["k"])
    ls = yh.option_symbol(root, x["e"], x["lk"]) if x["lk"] else None
    legs, c, e = (2 if ls else 1), x["credit"], x["e"]
    if ss not in ASK and ss not in BID and e >= TODAY:
        return None
    days = sorted(d for d in set(ASK.get(ss, {})) | set(BID.get(ss, {})) if x["d0"] < d <= min(TODAY, e))
    last = None
    for d in days:
        cost = max(leg(ss, d, True) - (leg(ls, d, False) if ls else 0.0), 0.0)
        last = (cost, d)
        if d == e:
            break
        sp = spot_at(x["t"], d)
        why = ("objetivo" if tp and cost <= c * (1 - tp / 100) else "stop" if stop and cost >= c * stop else
               "bajo el strike" if touch and sp is not None and sp < x["k"] else
               "revisión DTE" if review and (e - x["d0"]).days > review and (e - d).days <= review else None)
        if why:
            return (c - cost) * 100 - 2 * FEE * legs, "cerrada", why
    if e < TODAY:
        sp = spot_at(x["t"], e)
        if sp is None:
            return None
        intr = max(x["k"] - sp, 0) - (max(x["lk"] - sp, 0) if ls else 0)
        return (c - intr) * 100 - FEE * legs, "vencida", "en el dinero" if intr > 0 else "sin valor"
    if last is None:
        return None
    return (c - last[0]) * 100 - 2 * FEE * legs, "abierta", "valorada hoy"


def capital(x):
    return ((x["k"] - x["lk"]) - x["credit"]) * 100 if x["lk"] else x["k"] * 100


def profile_rule(x):
    if x["group"] in TPC_PROFILES:
        return {}
    return dict(tp=50, review=21, stop=2.0) if x["lk"] else dict(tp=50, review=21)


for x in entries.values():
    r = run(x, **profile_rule(x))
    x["pnl"], x["status"], x["reason"] = r if r else (None, None, None)
    if x["pnl"] is not None:
        x["roc"] = 100 * x["pnl"] / capital(x)
xs = [x for x in entries.values() if x["pnl"] is not None]
with open(f"{D}/registro_{MODE}.json", "w", encoding="utf-8") as f:
    json.dump([{k: (str(v) if isinstance(v, date) else v) for k, v in x.items()} for x in xs], f, ensure_ascii=False)

ORDER = ["Táctico (TPC)", "Mensual (TPC)", "TastyPut", "TastySpread", "Comunidad", "MarketRadar"]
groups = sorted({(x["group"], x["kind"]) for x in xs}, key=lambda g: (ORDER.index(g[0]) if g[0] in ORDER else 99, g[1]))
print(f"Valoración: {MODE} · último día con velas {TODAY} · {len(xs)} entradas ({len(entries) - len(xs)} sin datos)\n")
print(f"{'perfil / fuente':<26}{'n':>5}{'cerr/venc':>10}{'ganan %':>8}{'P&L cerr. $':>13}{'P&L abiertas $':>15}{'total $':>11}{'% prima':>9}{'% capital':>10}")
for g in groups:
    sel = [x for x in xs if (x["group"], x["kind"]) == g]
    done = [x for x in sel if x["status"] != "abierta"]
    tot = sum(x["pnl"] for x in sel)
    print(f"{g[0] + ' · ' + g[1]:<26}{len(sel):>5}{len(done):>10}{(100 * sum(x['pnl'] > 0 for x in done) / len(done) if done else 0):>8.0f}"
          f"{sum(x['pnl'] for x in done):>13,.0f}{sum(x['pnl'] for x in sel if x['status'] == 'abierta'):>15,.0f}{tot:>11,.0f}"
          f"{100 * tot / sum(x['credit'] * 100 for x in sel):>9.0f}{100 * tot / sum(capital(x) for x in sel):>10.2f}")


def stats(sel):
    if not sel:
        return "—"
    return (f"n={len(sel):>4}  ganan {100 * sum(x['pnl'] > 0 for x in sel) / len(sel):4.0f} %  P&L medio {mean(x['pnl'] for x in sel):>7.0f} $  "
            f"% capital medio {mean(x['roc'] for x in sel):6.2f}  mediana {median(x['roc'] for x in sel):6.2f}")


def buckets(name, f, cuts, sel):
    print(f"  {name}")
    vals = [x for x in sel if f(x) is not None]
    for lo, hi in zip(cuts, cuts[1:]):
        print(f"    {lo:>6} – {hi:<6} {stats([x for x in vals if lo <= f(x) < hi])}")


for kind in ("put", "spread"):
    sel = [x for x in xs if x["kind"] == kind]
    print(f"\n######## {kind.upper()}S · {stats(sel)}")
    buckets("distancia del strike (%)", lambda x: x["dist"], [0, 5, 10, 15, 20, 30, 100], sel)
    buckets("delta (abs)", lambda x: abs(x["delta"]) if x["delta"] is not None else None, [0, .10, .15, .20, .25, .30, .50, 1.01], sel)
    buckets("IV Rank", lambda x: x["ivr"], [0, 15, 30, 50, 75, 1000], sel)
    buckets("DTE al entrar", lambda x: x["dte"], [0, 8, 15, 22, 30, 38, 46, 100], sel)
    buckets("yield anualizado (%)", lambda x: x["yld"], [0, 15, 25, 40, 60, 100, 10000], sel)
    buckets("variación 5 sesiones previas (%)", lambda x: x["ret5"], [-100, -5, -2, 0, 2, 5, 100], sel)
    for lbl, f in (("resultados antes del vencimiento", lambda x: x["earn"]), ("ticker sobre su SMA50", lambda x: x["above50"]),
                   ("SPY sobre su SMA20", lambda x: x["spy_up"])):
        print(f"  {lbl}:  sí {stats([x for x in sel if f(x) is True])}\n  {' ' * len(lbl)}   no {stats([x for x in sel if f(x) is False])}")

print("\n######## SALIDAS ALTERNATIVAS (mismas entradas)")
variants = {
    "la del perfil": None,
    "objetivo 25 % / 21 DTE": dict(tp=25, review=21),
    "objetivo 50 % / 21 DTE": dict(tp=50, review=21),
    "objetivo 75 % / 21 DTE": dict(tp=75, review=21),
    "50 % + stop 2× crédito": dict(tp=50, review=21, stop=2.0),
    "50 % + stop 3× crédito": dict(tp=50, review=21, stop=3.0),
    "50 % + cierre bajo el strike": dict(tp=50, review=21, touch=True),
    "al vencimiento, sin gestión": dict(),
}
for g in groups:
    sel = [x for x in xs if (x["group"], x["kind"]) == g]
    print(f"\n  {g[0]} · {g[1]} (n={len(sel)})")
    for name, p in variants.items():
        res = [r for r in (run(x, **(profile_rule(x) if p is None else p)) for x in sel) if r]
        print(f"    {name:<32} {sum(r[0] for r in res):>10,.0f} $   ganan {100 * sum(r[0] > 0 for r in res) / len(res):4.0f} %   "
              f"cerradas/vencidas {sum(r[1] != 'abierta' for r in res):>4}")
