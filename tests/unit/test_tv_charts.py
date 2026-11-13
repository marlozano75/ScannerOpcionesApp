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
    data = tv_charts.candle_data(BarRepo(Database(":memory:")), "NADA")
    assert data["candles"] == [] and data["volume"] == [] and not tv_charts.needs_live(data, TODAY)


def test_niveles_de_put_spread_y_cds():
    assert [(lv.title, lv.price) for lv in tv_charts.put_levels(50.0, None, 1.25)] == [("Put vendida", 50.0), ("Breakeven", 48.75)]
    assert [lv.title for lv in tv_charts.put_levels(50.0, 45.0)] == ["Put vendida", "Put comprada"]
    assert [(lv.title, lv.price) for lv in tv_charts.cds_levels(140.0, 130.0, 150.0)] == [("Call vendida", 140.0), ("Call comprada", 130.0), ("Obj.", 150.0)]


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


def test_zona_de_revisión_apertura_y_último_toque_viajan_al_navegador():
    expiry, review = TODAY + timedelta(days=30), TODAY + timedelta(days=9)
    zone = tv_charts.review_zone(review, expiry, 21, True, TODAY)
    assert [(e.day, e.until, e.color) for e in zone] == [(review, expiry, tv_charts.REVIEW)] and "21 DTE" in zone[0].title
    assert tv_charts.review_zone(review, expiry, 21, False, TODAY)[0].title.startswith("Referencia 21 DTE")      # el perfil no revisa
    assert tv_charts.review_zone(None, expiry, 21, True, TODAY) == [] and tv_charts.review_zone(review, TODAY - timedelta(days=1), 21, True, TODAY) == []
    assert tv_charts.opening_marker(None) == [] and tv_charts.opening_marker(TODAY, False)[0].title == "Apertura (aprox.)"
    assert tv_charts.touch_marker(None) == [] and tv_charts.touch_marker((TODAY, 41.5, 12))[0].title == "Último toque · hace 12 d"
    html = str(tv_charts.candle_switch("<svg/>", "AAA", [], zone, tv_charts.opening_marker(TODAY), ["Prima <b>1,25</b>"]))
    attr = lambda name: json.loads(re.search(rf'data-{name}="([^"]*)"', html).group(1).replace("&#34;", '"'))      # noqa: E731
    assert attr("events") == [{"time": review.isoformat(), "title": zone[0].title, "color": tv_charts.REVIEW, "end": expiry.isoformat()}]
    assert attr("markers") == [{"time": TODAY.isoformat(), "title": "Apertura", "color": tv_charts.OPENED, "shape": "arrowUp", "below": True}]
    assert '<div class="tvw-notes"><span>Prima &lt;b&gt;1,25&lt;/b&gt;</span></div>' in html


def test_velas_intradía_en_hora_de_nueva_york_con_su_cambio_de_horario():
    summer, winter = 1_783_517_400, 1_794_407_400        # 2026-07-08 13:30 UTC (EDT, -4 h) y 2026-11-11 14:30 UTC (EST, -5 h): ambas a las 09:30 en NY
    data = tv_charts.intraday_data([(summer, 10.0, 10.4, 9.9, 10.2, 0.0), (winter, 20.0, 19.0, 19.5, 20.5, 300.0)], "AAA", "5m")
    first, second = data["candles"]
    assert first["time"] == summer - 4 * 3600 and second["time"] == winter - 5 * 3600
    assert first["session"] == "2026-07-08" and second["session"] == "2026-11-11"
    assert second["high"] == 20.5 and second["low"] == 19.5                  # máximo y mínimo abarcan apertura y cierre (datos incoherentes)
    assert data["volume"] == [{"time": second["time"], "value": 300.0, "up": True}]    # sin volumen no hay barra


def test_el_conmutador_ofrece_los_intervalos_intradía():
    html = str(tv_charts.candle_switch("<svg/>", "AAA"))
    assert 'data-i="1d"' in html and all(f'data-i="{k}"' in html for k in ("5m", "15m", "1h"))
    assert 'class="tvw-rng tvw-irng on" data-d="1" hidden' in html            # los rangos intradía se ven solo en esa vista


def test_pre_y_post_mercado_se_marcan_como_fuera_de_horario():
    base = 1_794_407_400                                   # 2026-11-11 09:30 NY (EST)
    at = lambda minutes: base + minutes * 60               # noqa: E731
    rows = [(at(m), 10.0, 10.0, 10.0, 10.0, 1.0) for m in (-335, -330, -5, 0, 385, 390, 625, 630)]   # 3:55, 4:00, 9:25, 9:30, 15:55, 16:00, 19:55, 20:00
    data = tv_charts.intraday_data(rows, "AAA", "5m", extended=True)
    assert [c.get("ext") for c in data["candles"]] == ["pre", "pre", None, None, "post", "post"] and data["extended"]     # sin la nocturna
    assert not any(c.get("ext") for c in tv_charts.intraday_data(rows[2:6], "AAA", "5m")["candles"])     # sin pre/post no se marca nada
    hourly = tv_charts.intraday_data([(at(-30), 10.0, 10.0, 10.0, 10.0, 1.0), (at(-90), 10.0, 10.0, 10.0, 10.0, 1.0)], "AAA", "1h", extended=True)
    assert [bool(c.get("ext")) for c in hourly["candles"]] == [False, True]      # la de 9:00-10:00 incluye la apertura
    assert 'class="tvw-ext"' in str(tv_charts.candle_switch("<svg/>", "AAA"))


def test_objetivo_del_cds_vencimiento_junto_al_débito_ganancia_en_la_salida_y_líneas_de_soporte():
    from scanner_opciones.metrics.support_levels import SupportLevel
    out = tv_charts.cds_levels(95.0, 85.0, 80.0)
    assert out[-1].title == "Obj." and out[-1].price == 80.0                                    # corto: no tapa las velas
    assert tv_charts.cds_note(1.5, date(2028, 1, 21), date(2027, 1, 21)) == "Débito que se paga: 1.50 $ · 150.00 $ por contrato · vto Ene 21 '28 (365 DTE)"
    ev = tv_charts.exit_event(date(2027, 1, 21), 10, 1234.4)
    assert ev == [tv_charts.Event(date(2027, 1, 31), "Salida prevista Ene 31 '27 · 10 d · ganancia +1.234 $", tv_charts.EXIT, label="Salida +1.234 $")]
    assert tv_charts.exit_event(date(2027, 1, 21), 10, -50.0)[0].label == "Salida −50 $"
    assert tv_charts.exit_event(date(2027, 1, 21), None, 50.0) == []
    assert '"label": "Salida +1.234 $"' in str(tv_charts.candle_switch("<svg/>", "AAA", events=ev)).replace("&#34;", '"')
    sup = SupportLevel(64.1, 65.88, 3, 4, 3, TODAY)
    assert tv_charts.support_lines([sup]) == [{"price": 65.88, "title": "Soporte · 3 toques", "color": tv_charts.SUPPORT}]
    assert 'data-i="1w"' in str(tv_charts.candle_switch("<svg/>", "AAA"))                       # velas semanales


def test_la_vela_de_hoy_sustituye_al_cierre_provisional():
    repo = BarRepo(Database(":memory:"))
    yesterday = TODAY - timedelta(days=1)
    repo.upsert_ohlc("AAA", [(yesterday, 10.0, 10.5, 9.5, 9.8, 100.0)])
    repo.upsert_provisional("AAA", TODAY, 10.4)
    data = tv_charts.candle_data(repo, "AAA")
    assert data["flat_last"] and tv_charts.needs_live(data, TODAY)
    tv_charts.merge_live(data, (TODAY, 10.4, 10.9, 10.1, 10.2, 50.0))          # día, cierre, máximo, mínimo, apertura, volumen
    assert data["candles"][-1] == {"time": TODAY.isoformat(), "open": 10.2, "high": 10.9, "low": 10.1, "close": 10.4} and data["live"]
    assert data["volume"][-1] == {"time": TODAY.isoformat(), "value": 50.0, "up": True} and len(data["candles"]) == 2
    old = tv_charts.merge_live(tv_charts.candle_data(repo, "AAA"), (yesterday - timedelta(days=1), 9.0, 9.0, 9.0, 9.0, 1.0))
    assert "live" not in old                                                     # una vela anterior a la última no cambia nada
    repo2 = BarRepo(Database(":memory:"))
    repo2.upsert_ohlc("BBB", [(yesterday, 10.0, 10.5, 9.5, 9.8, 100.0)])
    full = tv_charts.candle_data(repo2, "BBB")
    assert not full["flat_last"] and tv_charts.needs_live(full, TODAY) is (TODAY.weekday() < 5)   # falta la de hoy en un día laborable
    assert not tv_charts.needs_live(full, yesterday)


def test_resultados_con_la_E_y_medias_EMA10_y_SMA20():
    ev = tv_charts.date_events(None, TODAY + timedelta(days=10), TODAY)
    assert ev[0].badge == "E"
    html = str(tv_charts.candle_switch("<svg/>", "AAA", events=ev))
    marks = json.loads(re.search(r'data-events="([^"]*)"', html).group(1).replace("&#34;", '"'))
    assert marks[0]["badge"] == "E"
    assert 'data-t="ema" data-n="10"' in html and 'class="tvw-ma" data-n="20"' in html
