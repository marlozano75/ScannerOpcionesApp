"""Vista de velas de los gráficos ampliados (`ui/tv_charts.py`): datos de las velas, niveles y conmutador."""
import json
import re
from datetime import date, timedelta

from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import BarRepo
from scanner_opciones.ui import tv_charts

TODAY = date(2026, 11, 12)


def test_velas_con_volumen_y_cierre_provisional_como_vela_plana():
    repo = BarRepo(Database(":memory:"))
    days = [TODAY - timedelta(days=i) for i in (3, 2, 1)]
    repo.upsert_ohlc("AAA", [(d, 10.5 + i, 11.0 + i, 9.0 + i, 10.0 + i, 1000.0) for i, d in enumerate(days)])      # día, cierre, máximo, mínimo, apertura, volumen
    repo.upsert_provisional("AAA", TODAY, 13.0)
    data = tv_charts.candle_data(repo, "AAA")
    assert [c["time"] for c in data["candles"]] == [d.isoformat() for d in days] + [TODAY.isoformat()]
    assert data["candles"][0] == {"time": days[0].isoformat(), "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5}
    assert data["candles"][-1] == {"time": TODAY.isoformat(), "open": 13.0, "high": 13.0, "low": 13.0, "close": 13.0}     # provisional: sin vela
    assert data["volume"][0] == {"time": days[0].isoformat(), "value": 1000.0, "up": True} and len(data["volume"]) == 3     # el provisional no tiene


def test_las_velas_anteriores_a_un_split_se_reescalan_como_sus_cierres():
    repo = BarRepo(Database(":memory:"))
    before = [(TODAY - timedelta(days=10 - i), 102.0, 104.0, 98.0, 100.0) for i in range(5)]      # cotizaba a ~100 antes de un split 4:1 sin ajustar
    after = [(TODAY - timedelta(days=5 - i), 25.5, 26.0, 24.5, 25.0) for i in range(5)]
    repo.upsert_ohlc("SPL", before + after)
    data = tv_charts.candle_data(repo, "SPL")
    first, last = data["candles"][0], data["candles"][-1]
    assert last["close"] == 25.5 and abs(first["close"] - 25.5) < 0.01          # el cierre limpio ya viene reescalado
    assert abs(first["high"] - 26.0) < 0.01 and abs(first["low"] - 24.5) < 0.01 and abs(first["open"] - 25.0) < 0.01
    assert all(c["low"] <= min(c["open"], c["close"]) and c["high"] >= max(c["open"], c["close"]) for c in data["candles"])


def test_un_ticker_sin_velas_devuelve_listas_vacías():
    assert tv_charts.candle_data(BarRepo(Database(":memory:")), "NADA") == {"ticker": "NADA", "candles": [], "volume": []}


def test_niveles_de_put_spread_y_cds():
    assert [(lv.title, lv.price) for lv in tv_charts.put_levels(50.0, None, 1.25)] == [("Put vendida", 50.0), ("Breakeven", 48.75)]
    assert [lv.title for lv in tv_charts.put_levels(50.0, 45.0)] == ["Put vendida", "Put comprada"]
    assert [(lv.title, lv.price) for lv in tv_charts.cds_levels(140.0, 130.0, 150.0)] == [("Call vendida", 140.0), ("Call comprada", 130.0), ("Objetivo", 150.0)]


def test_solo_se_marcan_las_fechas_que_no_han_pasado():
    events = tv_charts.date_events(TODAY + timedelta(days=30), TODAY - timedelta(days=1), TODAY)
    assert [e.title for e in events] == [f"Vencimiento {TODAY + timedelta(days=30):%d/%m} · 30 d"]


def test_el_conmutador_conserva_el_gráfico_de_cierres_y_escapa_los_datos():
    html = str(tv_charts.candle_switch("<svg id='cierres'></svg>", 'BRK "B"', [tv_charts.Level(43.0, "Put <vendida>")],
                                       [tv_charts.Event(TODAY, "Vencimiento")]))
    assert "<svg id='cierres'></svg>" in html and 'data-v="tv"' in html and 'class="tvw-tv" hidden' in html
    assert 'data-ticker="BRK &#34;B&#34;"' in html and "<vendida>" not in html
    lines = json.loads(re.search(r'data-lines="([^"]*)"', html).group(1).replace("&#34;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    assert lines == [{"price": 43.0, "title": "Put <vendida>", "color": tv_charts.REFERENCE}]
    assert "TradingView Lightweight Charts" in html                 # la licencia pide la atribución


def test_la_librería_va_en_el_paquete():
    assert tv_charts.LIBRARY.is_file() and (tv_charts.STATIC_DIR / "lightweight-charts.LICENSE").is_file()
    assert "Lightweight Charts" in tv_charts.LIBRARY.read_text(encoding="utf-8")[:300]
