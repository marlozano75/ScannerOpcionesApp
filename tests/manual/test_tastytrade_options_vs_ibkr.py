"""Prueba de viabilidad (solo lectura): ¿pueden la cadena y las cotizaciones de opciones venir de tastytrade
en lugar de IBKR? Mide tiempo, cobertura y diferencias de valores. No modifica la app ni envía órdenes.

    .venv/Scripts/python -m pytest tests/manual/test_tastytrade_options_vs_ibkr.py -m manual -s

Requiere tastytrade.client_secret/refresh_token en config/config.yaml. La comparación con IBKR necesita TWS
abierto (clientId = config + 100); si no está, se hace solo la parte de tastytrade.
Variables de entorno opcionales:
    SMOKE_TICKERS       tickers separados por coma (por defecto AAPL,MU,BAC,PBR-A,AXTI,ADAM)
    SMOKE_BATCH_SIZES   tamaños de lote DXLink a probar (por defecto 50,100,200,400)
    SMOKE_WAIT_SECONDS  espera máxima por lote de cotizaciones (por defecto 20)
    SMOKE_SKIP_IBKR     «1» para no conectar con TWS y medir solo tastytrade
Con el mercado cerrado, las cotizaciones en vivo pueden llegar vacías en ambos lados: interpreta solo la
cobertura de contratos (cadena) y, para valores, ejecuta la prueba con el mercado abierto.
"""
import asyncio
import math
import os
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import CandidateRange, load_settings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionChain, OptionContract
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, tasty_symbol
from scanner_opciones.scanner.candidates import candidate_contracts

pytestmark = [pytest.mark.manual, pytest.mark.asyncio]
CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
TICKERS = [t.strip().upper() for t in os.environ.get("SMOKE_TICKERS", "AAPL,MU,BAC,PBR-A,AXTI,ADAM").split(",") if t.strip()]
BATCH_SIZES = [int(x) for x in os.environ.get("SMOKE_BATCH_SIZES", "50,100,200,400").split(",")]
WAIT = float(os.environ.get("SMOKE_WAIT_SECONDS", "20"))
SETTLE = 3.0   # segundos sin datos nuevos tras los cuales se da el lote por terminado


def num(x) -> Optional[float]:
    """Decimal/float de dxFeed -> float; NaN, infinito o ≤ 0 en precios -> None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def key(expiry: date, strike: float) -> tuple[date, float]:
    return expiry, round(float(strike), 2)


async def tasty_puts(session, ticker: str, rng: CandidateRange, price: float, today: date) -> dict[tuple[date, float], str]:
    """Puts reales de la cadena de tastytrade dentro de la ventana de la app: (vencimiento, strike) -> símbolo DXLink."""
    from tastytrade.instruments import OptionType, get_option_chain

    chain = await get_option_chain(session, tasty_symbol(ticker))
    out: dict[tuple[date, float], str] = {}
    for expiry, options in chain.items():
        if not (rng.dte_min <= (expiry - today).days <= rng.dte_max):
            continue
        for o in options:
            if o.option_type != OptionType.PUT:
                continue
            strike = float(o.strike_price)
            below = (price - strike) / price * 100
            if rng.strike_below_pct_min <= below <= rng.strike_below_pct_max:
                out[key(expiry, strike)] = o.streamer_symbol
    return out


async def dx_quotes(session, symbols: list[str], wait: float) -> tuple[dict[str, dict], float]:
    """Quote + Greeks + Summary por DXLink para `symbols`. Termina al tener Quote y Greeks de todos, al pasar
    SETTLE s sin datos nuevos o al agotar `wait`. Devuelve ({símbolo: valores}, segundos hasta la última llegada)."""
    from tastytrade.dxfeed import Greeks, Quote, Summary
    from tastytrade.streamer import DXLinkStreamer

    got: dict[str, dict] = {s: {} for s in symbols}
    t0 = time.perf_counter()

    async def read(event_class, fill) -> None:
        async for ev in streamer.listen(event_class):
            if ev.event_symbol in got:
                fill(got[ev.event_symbol], ev)

    def fill_quote(d, e):
        d.update(bid=num(e.bid_price), ask=num(e.ask_price), bid_size=num(e.bid_size))

    def fill_greeks(d, e):
        d.update(iv=num(e.volatility), delta=num(e.delta))

    def fill_summary(d, e):
        d.update(oi=num(e.open_interest))

    async with DXLinkStreamer(session) as streamer:
        for cls in (Quote, Greeks, Summary):
            await streamer.subscribe(cls, symbols)
        tasks = [asyncio.create_task(read(c, f)) for c, f in ((Quote, fill_quote), (Greeks, fill_greeks), (Summary, fill_summary))]
        try:
            deadline, last_n, last_change = t0 + wait, -1, t0
            while time.perf_counter() < deadline:
                n = sum(1 for d in got.values() if "bid" in d and "iv" in d)
                now = time.perf_counter()
                if n != last_n:
                    last_n, last_change = n, now
                if n == len(got) or (n > 0 and now - last_change > SETTLE):
                    break          # completo, o sin datos nuevos desde hace SETTLE s (el resto no cotiza)
                await asyncio.sleep(0.25)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return got, last_change - t0


def med(values: list[float]) -> str:
    return f"{statistics.median(values):.4f}" if values else "-"


@pytest.fixture(scope="module")
def settings():
    return load_settings(CONFIG)


@pytest.fixture
def tt(settings):   # una sesión por test: la del SDK queda atada al event loop que la creó
    c = settings.tastytrade
    return TastytradeVolatility(c.client_secret, c.refresh_token)


async def test_chain_and_quotes_tastytrade_vs_ibkr(settings, tt):
    rng = settings.scanner.candidates
    today = date.today()
    session = tt._open_session()
    prices = await tt.get_prices(TICKERS)
    print(f"\nVentana: DTE {rng.dte_min}-{rng.dte_max}, strike -{rng.strike_below_pct_min}%…-{rng.strike_below_pct_max}%")
    print(f"Precios tastytrade: {prices}")

    # --- tastytrade: cadena -------------------------------------------------------------------------------
    tasty: dict[str, dict[tuple[date, float], str]] = {}
    print("\n[tastytrade] cadena (contratos reales dentro de la ventana)")
    for t in TICKERS:
        if t not in prices:
            print(f"  {t:6} sin precio en tastytrade; se omite")
            continue
        t0 = time.perf_counter()
        tasty[t] = await tasty_puts(session, t, rng, prices[t], today)
        print(f"  {t:6} {len(tasty[t]):4d} puts en {time.perf_counter() - t0:5.2f} s")
    assert tasty and any(tasty.values()), "tastytrade no devolvió ninguna cadena"

    # --- tastytrade: cotizaciones ------------------------------------------------------------------------
    quotes_tt: dict[str, dict[str, dict]] = {}
    print(f"\n[tastytrade] cotizaciones DXLink (espera máx. {WAIT:.0f} s por ticker)")
    for t, puts in tasty.items():
        symbols = list(puts.values())
        if not symbols:
            continue
        try:
            got, secs = await dx_quotes(session, symbols, WAIT)
        except Exception as exc:   # límites de suscripción, red…
            print(f"  {t:6} {len(symbols)} contratos -> ERROR {type(exc).__name__}: {str(exc)[:120]}")
            continue
        quotes_tt[t] = got
        withq = sum(1 for d in got.values() if d.get("bid") is not None or d.get("ask") is not None)
        withg = sum(1 for d in got.values() if d.get("iv") is not None)
        withoi = sum(1 for d in got.values() if d.get("oi") is not None)
        print(f"  {t:6} {len(symbols):4d} contratos en {secs:5.1f} s | bid/ask {withq} | IV/delta {withg} | OI {withoi}")
    assert any(quotes_tt.values()), "tastytrade no devolvió ninguna cotización por DXLink"

    # --- IBKR (opcional) ----------------------------------------------------------------------------------
    if os.environ.get("SMOKE_SKIP_IBKR") == "1":
        print("\n[IBKR] omitido (SMOKE_SKIP_IBKR=1)")
        return
    ibkr = settings.ibkr
    gw = IBKRGateway(ibkr.model_copy(update={"client_id": ibkr.client_id + 100}))
    try:
        await asyncio.wait_for(gw.connect(), timeout=30)
    except Exception as exc:
        print(f"\n[IBKR] sin conexión ({type(exc).__name__}); se omite la comparación")
        return
    try:
        print("\n[IBKR] cadena + validación de las mismas ventanas (como hace la app)")
        diffs: dict[str, list[float]] = {"bid": [], "ask": [], "iv": [], "delta": [], "oi": [], "bid_size": []}
        ib_secs_total = 0.0
        n_quoted = 0
        for t, puts in tasty.items():
            t0 = time.perf_counter()
            chain: OptionChain = await gw.get_option_chain(t)
            cands = candidate_contracts(chain, prices[t], today, rng)
            qualified = await gw.qualify_contracts(cands)
            secs_val = time.perf_counter() - t0
            ib_keys = {key(c.expiry, c.strike): c for c in qualified}
            tt_keys = set(puts)
            only_tt, only_ib = tt_keys - set(ib_keys), set(ib_keys) - tt_keys
            print(f"  {t:6} candidatos {len(cands):4d} -> IBKR válidos {len(ib_keys):4d} en {secs_val:5.1f} s | "
                  f"tastytrade {len(tt_keys):4d} | solo tasty {len(only_tt)} | solo IBKR {len(only_ib)}")

            common = [ib_keys[k] for k in sorted(tt_keys & set(ib_keys))]
            if not common or t not in quotes_tt:
                continue
            t0 = time.perf_counter()
            ib_q = await gw.get_quotes(common)
            ib_secs = time.perf_counter() - t0
            ib_secs_total += ib_secs
            n_quoted += len(common)
            for c in common:
                q, d = ib_q.get(c), quotes_tt[t].get(puts[key(c.expiry, c.strike)], {})
                if q is None:
                    continue
                pairs = {"bid": (q.bid, d.get("bid")), "ask": (q.ask, d.get("ask")), "iv": (q.iv, d.get("iv")),
                         "delta": (q.delta, d.get("delta")), "oi": (q.open_interest, d.get("oi")),
                         "bid_size": (q.bid_size, d.get("bid_size"))}
                for name, (a, b) in pairs.items():
                    if a is None or b is None:
                        continue
                    diffs[name].append(abs(abs(float(a)) - abs(float(b))))   # |delta|: el signo no importa aquí
            print(f"         cotización IBKR de {len(common)} contratos en {ib_secs:5.1f} s")

        print(f"\n[IBKR vs tastytrade] diferencia absoluta mediana por campo ({n_quoted} contratos comunes, "
              f"IBKR {ib_secs_total:.1f} s en cotizar)")
        for name, vals in diffs.items():
            print(f"  {name:9} n={len(vals):4d}  mediana {med(vals):>9}  máx {max(vals):.4f}" if vals else
                  f"  {name:9} n=   0  (sin datos comparables)")
        print("  Nota: IV en fracción (0.35 = 35 %); si IBKR da % habría que alinear antes de concluir.")
    finally:
        await gw.disconnect()


async def test_dxlink_option_batch_sizes(settings, tt):
    """¿Cuántas suscripciones de opciones admite DXLink de una vez? (las velas fallaron por encima de 50)."""
    rng = settings.scanner.candidates
    session = tt._open_session()
    ticker = TICKERS[0]
    price = (await tt.get_prices([ticker]))[ticker]
    # Ampliamos la ventana para tener suficientes contratos con los que llenar el lote más grande.
    wide = CandidateRange(strike_below_pct_min=0, strike_below_pct_max=60, dte_min=0, dte_max=max(rng.dte_max, 120))
    symbols = list((await tasty_puts(session, ticker, wide, price, date.today())).values())
    print(f"\n{ticker}: {len(symbols)} puts disponibles para la prueba de lotes")
    for size in BATCH_SIZES:
        batch = symbols[:size]
        if len(batch) < size:
            print(f"  lote {size:4d}: solo hay {len(batch)} contratos; se omite")
            continue
        try:
            got, secs = await dx_quotes(session, batch, WAIT)
        except Exception as exc:
            print(f"  lote {size:4d}: ERROR {type(exc).__name__}: {str(exc)[:150]}")
            continue
        ok = sum(1 for d in got.values() if "bid" in d and "iv" in d)
        print(f"  lote {size:4d}: {ok}/{size} completos en {secs:5.1f} s")
