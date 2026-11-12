"""Gráfico de velas interactivo (TradingView Lightweight Charts™, Apache 2.0) para los gráficos ampliados del Scanner, el Panel y el Universo.

El gráfico de cierres en SVG de siempre se queda como estaba; `candle_switch` lo envuelve con un conmutador «Cierres / Velas». La vista de velas
se dibuja en el navegador (script de `base.html`) con la librería servida desde el propio paquete (`ui/static/lightweight-charts.js`, sin CDN;
se carga la primera vez que se abre una vista de velas) y los datos de `candle_data` (`GET /chart/candles`). Los niveles (strikes, breakeven,
objetivo) van como líneas de precio y las fechas (vencimiento, resultados) como bandas verticales.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from markupsafe import Markup, escape

from scanner_opciones.storage.repositories import BarRepo

STATIC_DIR = Path(__file__).parent / "static"
LIBRARY = STATIC_DIR / "lightweight-charts.js"

# colores de las líneas y bandas (los mismos papeles que en los gráficos SVG: vendida, comprada, referencia, vencimiento, resultados)
SHORT, LONG, REFERENCE, EXPIRY, EARNINGS = "#d64545", "#1f9d55", "#8b94a3", "#1f2d4a", "#d9822b"
RANGES = ((1, "1 M"), (3, "3 M"), (6, "6 M"), (12, "1 A"), (24, "2 A"), (0, "Todo"))      # meses visibles (0 = todo el histórico)
DEFAULT_RANGE = 6


@dataclass(frozen=True)
class Level:
    """Nivel de precio que se dibuja como línea horizontal."""
    price: float
    title: str
    color: str = REFERENCE


@dataclass(frozen=True)
class Event:
    """Fecha que se marca con una banda vertical (puede ser futura: el eje se alarga hasta ella)."""
    day: date
    title: str
    color: str = EXPIRY


def candle_data(bars: BarRepo, ticker: str) -> dict:
    """Velas y volumen de un ticker para la vista de velas: {candles: [{time, open, high, low, close}], volume: [{time, value, up}]}.

    Parte de los cierres LIMPIOS (los mismos que usan los demás gráficos y los filtros: sin picos de split y con lo anterior a un split
    reescalado; incluyen el cierre provisional del día) y aplica a la apertura, el máximo y el mínimo guardados el mismo factor que llevó su
    cierre. Un día sin vela completa guardada (o el provisional de hoy) sale como una vela plana en su cierre. El volumen es el de tastytrade
    (≈30-40 % del consolidado): sirve en relativo."""
    key = ticker.replace(" ", "-")
    clean = bars.closes(key)
    raw = {c[0]: c for c in bars.candles(key)}
    volumes = bars.volumes(key)
    candles, volume = [], []
    for day in sorted(clean):
        close = clean[day]
        r = raw.get(day)
        if r is not None and r[4] and r[4] > 0 and None not in (r[1], r[2], r[3]):
            factor = close / r[4]
            opened, high, low = r[1] * factor, r[2] * factor, r[3] * factor
            high, low = max(high, opened, close), min(low, opened, close)
        else:
            opened = high = low = close
        iso = day.isoformat()
        candles.append({"time": iso, "open": round(opened, 4), "high": round(high, 4), "low": round(low, 4), "close": round(close, 4)})
        if volumes.get(day):
            volume.append({"time": iso, "value": volumes[day], "up": close >= opened})
    return {"ticker": ticker, "candles": candles, "volume": volume}


def candle_switch(closes_chart, ticker: str, levels: Sequence[Level] = (), events: Sequence[Event] = ()) -> Markup:
    """Envuelve el gráfico de cierres (`closes_chart`, HTML) con el conmutador «Cierres / Velas» y la caja vacía de la vista de velas."""
    lines = [{"price": round(float(lv.price), 4), "title": lv.title, "color": lv.color} for lv in levels if lv.price and lv.price > 0]
    marks = [{"time": ev.day.isoformat(), "title": ev.title, "color": ev.color} for ev in events]
    ranges = "".join(f'<button type="button" class="tvw-rng{" on" if m == DEFAULT_RANGE else ""}" data-m="{m}">{label}</button>' for m, label in RANGES)
    chips = "".join(f'<span class="tvw-chip" style="--c:{escape(ev.color)}">{escape(ev.title)}</span>' for ev in events)
    return Markup(
        f'<div class="tvw" data-ticker="{escape(ticker)}" data-lines="{escape(json.dumps(lines))}" data-events="{escape(json.dumps(marks))}">'
        '<div class="tvw-bar"><button type="button" class="tvw-btn on" data-v="svg" title="Gráfico de cierres con sus anotaciones">Cierres</button>'
        '<button type="button" class="tvw-btn" data-v="tv" title="Velas diarias interactivas (zoom con la rueda, arrastrar para desplazar)">Velas</button></div>'
        f'<div class="tvw-svg">{closes_chart}</div>'
        '<div class="tvw-tv" hidden>'
        f'<div class="tvw-tools">{ranges}<label class="tvw-opt"><input type="checkbox" class="tvw-ma" data-n="50"> SMA 50</label>'
        '<label class="tvw-opt"><input type="checkbox" class="tvw-ma" data-n="200"> SMA 200</label>'
        '<button type="button" class="tvw-log" aria-pressed="false" title="Escala logarítmica del eje de precios">Log</button></div>'
        f'<div class="tvw-legend">&nbsp;</div><div class="tvw-chart"></div><div class="tvw-foot">{chips}'
        '<a class="tvw-credit" href="https://www.tradingview.com/" target="_blank" rel="noopener">Gráfico: TradingView Lightweight Charts™</a></div>'
        '</div></div>')


def put_levels(strike: float, long_strike: Optional[float] = None, premium: Optional[float] = None) -> list[Level]:
    """Líneas de una put vendida (o un spread de puts): vendida, comprada y, si se conoce la prima o el crédito por acción, el breakeven."""
    out = [Level(strike, "Put vendida", SHORT)]
    if long_strike is not None:
        out.append(Level(long_strike, "Put comprada", LONG))
    if premium is not None and 0 < premium < strike:
        out.append(Level(strike - premium, "Breakeven", REFERENCE))
    return out


def cds_levels(short_strike: float, long_strike: Optional[float], exit_price: Optional[float] = None) -> list[Level]:
    """Líneas de un Call Debit Spread: call comprada, call vendida y el precio objetivo de salida."""
    out = [Level(short_strike, "Call vendida", SHORT)]
    if long_strike is not None:
        out.append(Level(long_strike, "Call comprada", LONG))
    if exit_price is not None:
        out.append(Level(exit_price, "Objetivo", REFERENCE))
    return out


def date_events(expiry: Optional[date], earnings: Optional[date], today: date) -> list[Event]:
    out = []
    if expiry is not None and expiry >= today:
        out.append(Event(expiry, f"Vencimiento {expiry:%d/%m} · {(expiry - today).days} d", EXPIRY))
    if earnings is not None and earnings >= today:
        out.append(Event(earnings, f"Resultados {earnings:%d/%m} · {(earnings - today).days} d", EARNINGS))
    return out
