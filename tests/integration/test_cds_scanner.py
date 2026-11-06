"""Perfil «MTO CDS» como tercer instrumento del Scanner (`/scanner?mode=cds`): mismas secciones, chips, panel de impacto y tabla que los demás perfiles."""
from datetime import timedelta

from fastapi.testclient import TestClient

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner import cds_profile
from scanner_opciones.ui.web import create_app
from tests.integration.test_service import NOW, TODAY, make_service

EXP = TODAY + timedelta(days=300)


def _call(strike, bid, ask, delta):
    return ContractSnapshot(OptionContract("ZZZ", EXP, strike, OptionRight.CALL, 100), NOW, bid=bid, ask=ask, delta=delta, iv=0.5, open_interest=100,
                            spread_pct=(ask - bid) / ((ask + bid) / 2) * 100, iv_rank=40.0, iv_percentile=55.0)


def _svc(enabled=True):
    svc, _ = make_service()
    cds = CdsSettings(enabled=enabled, monthly_only=False, sma200_below_pct_min=None, min_max_return_pct=None, width_options=(10.0,), min_debit_usd=0, max_debit_usd=10000)
    svc.settings = svc.settings.model_copy(update={"scanner": svc.settings.scanner.model_copy(update={"cds": cds})})
    return svc


def _seed(svc):
    """Un CDS 130/140 guardado y cotizado (después del arranque: la actualización diaria reescribe el catálogo)."""
    svc.watchlist.add(["ZZZ"], NOW)
    svc.ticker_info.upsert(TickerInfo("ZZZ", sector="Technology", underlying_price=100.0, iv30=50.0))
    snaps = [_call(130, 3.9, 4.0, 0.30), _call(140, 2.2, 2.4, 0.22)]
    svc.contracts.sync_for_ticker("ZZZ", {svc.contracts.key(s.contract) for s in snaps}, [s.contract for s in snaps])
    assert svc.snapshots.upsert_many(snaps) == 2


def _client(svc):
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    return TestClient(app)


def test_cds_profile_renders_in_the_scanner_with_its_filters_chips_impact_and_table():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        _seed(svc)
        page = client.get("/scanner?reset=1")
        assert page.status_code == 200
        # el perfil CDS está en la barra de perfiles del propio Scanner y su enlace no sale de /scanner
        assert 'href="/scanner?' in page.text and "mode=cds" in page.text and 'href="/cds' not in page.text
        text = client.get("/scanner?" + cds_profile_query(svc)).text
        for needle in ('id="sec-widths"', 'id="sec-debit"', 'id="sec-move"', 'name="dmax"', 'Base (', 'Activados (', 'Desactivados (', 'id="viz-box"', 'data-kind="cds"',
                       '>ZZZ</a>', 'Comp./Vend.', '× esperado', 'Technology',
                       'name="oi_s"', 'name="ls_s"', 'name="use_bsz"', 'name="use_asz"', 'Bid size comp.', 'Ask size comp.', 'Bid size vend.', 'Ask size vend.'):
            assert needle in text, needle
        assert "Simular seleccionados" not in text and "Téc." not in text
        assert text.count("<tr><td class=\"tk\"") == 1         # el único spread 130/140
        impact = client.get("/scanner/impact?mode=cds&submitted=1&profile=" + cds_profile.MTO_CDS).text
        assert "Débito por contrato" in impact and "Movimiento necesario" in impact and "spreads" in impact
        mini = client.get("/chart/mini.svg?ticker=ZZZ&strike=140&long=130&kind=cds")
        assert mini.status_code == 200 and mini.text.startswith("<svg")


def cds_profile_query(svc):
    from scanner_opciones.ui.scanner_forms import profile_link
    return profile_link(next(p for p in svc.profile_provider()[0] if p.mode == "cds"))


def test_old_cds_address_redirects_to_the_scanner_and_save_goes_back_to_it():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        old = client.get("/cds", follow_redirects=False)
        assert old.status_code == 303 and old.headers["location"].startswith("/scanner?") and "mode=cds" in old.headers["location"]
        old = client.get("/cds?dmax=700&submitted=1", follow_redirects=False)
        assert old.headers["location"].startswith("/scanner?") and "dmax=700" in old.headers["location"] and "mode=cds" in old.headers["location"]
        saved = client.post("/scanner/profile/save", data={"name": "CDS barato", "query": "mode=cds&submitted=1&dmax=700&widths=10&best=on&dmin=0"}, follow_redirects=False)
        assert saved.status_code == 303 and saved.headers["location"].startswith("/scanner?") and "mode=cds" in saved.headers["location"]


def test_disabled_cds_shows_the_notice_and_no_table():
    svc = _svc(enabled=False)
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        text = client.get("/scanner?" + cds_profile_query(svc)).text
        assert "scanner.cds.enabled" in text and 'id="scan-results"' not in text and 'name="dmax"' in text
        assert client.get("/scanner/impact?mode=cds&submitted=1").status_code == 200


def test_company_and_ticker_filters_apply_show_chips_and_count_in_the_impact_panel():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        _seed(svc)                                     # ZZZ no tiene EPS ni liquidez guardados
        base = cds_profile_query(svc)
        assert '<td class="tk"' in client.get("/scanner?" + base).text
        for extra, chip in (("&q_profit=on", "Beneficios 12 m"), ("&q_liq=3", "Liquidez opc."), ("&q_fcf=on", "FCF &gt; 0"), ("&price_min=200", "Subyacente $200")):
            text = client.get("/scanner?" + base + extra).text
            assert '<td class="tk"' not in text and 'name="q_profit"' in text, extra            # ZZZ (precio 100, sin datos) queda fuera
            assert chip.replace("&gt;", ">") in text.replace("&gt;", ">"), extra
        impact = client.get("/scanner/impact?" + base + "&q_profit=on&price_max=500").text
        assert "Beneficios (12 meses)" in impact and "Precio del subyacente" in impact
        # un precio que sí admite al ticker lo deja pasar
        assert '<td class="tk"' in client.get("/scanner?" + base + "&price_min=50&price_max=500").text
        # se guardan en el perfil y vuelven al cargarlo
        client.post("/scanner/profile/save", data={"name": "CDS sano", "query": base + "&q_profit=on&q_quarters=3"})
        prof = next(p for p in svc.stored_profiles()[0] if p.name == "CDS sano")
        assert prof.cds["q_profit"] == "on" and prof.cds["q_quarters"] == "3"
        page = client.get("/scanner?mode=cds&profile=CDS+sano").text
        assert 'name="q_profit" checked' in page


def test_screen_report_lists_the_tickers_that_cannot_reach_any_put_profile_and_changes_nothing():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        svc.watchlist.add(["LOW"], NOW)
        svc.ticker_info.upsert(TickerInfo("LOW", sector="Utilities", underlying_price=100.0, iv30=3.0))      # IV del 3 %: no hay prima
        svc.ticker_lists = lambda: {"LOW": ["Fuente A"]}
        page = client.get("/scanner/criba")
        assert page.status_code == 200
        for needle in ("Criba teórica", "descartables", "LOW", "Fuente A", "Teórico / exigido"):
            assert needle in page.text, needle
        assert svc.watchlist.list() == ["LOW"] or "LOW" in svc.watchlist.list()          # solo informa
        assert "/scanner/criba" in client.get("/scanner/config").text


class _FakeBidHistory:
    """Proveedor de bids históricos de prueba: bid fijo por símbolo; los de `silent` no responden y los de `empty` responden sin ninguna vela con bid."""

    def __init__(self, bids, silent=(), empty=()):
        self.bids, self.silent, self.empty, self.requests = bids, set(silent), set(empty), []

    async def option_bid_history(self, symbols, days, on_progress=None):
        self.requests.append(list(symbols))
        if on_progress is not None:
            on_progress(len(symbols) - len(self.silent & set(symbols)), len(symbols), 1)
        out = {}
        for s in symbols:
            if s in self.silent:
                continue
            out[s] = {} if s in self.empty else {TODAY - timedelta(days=d): self.bids.get(s, 0.0) for d in range(0, days) if self.bids.get(s, 0.0) > 0}
        return out


def test_bid_history_is_requested_once_stored_and_judged_by_months_that_reach_the_yield():
    from scanner_opciones.domain.models import TickerInfo as TI
    from scanner_opciones.scanner import yield_history as yh

    svc = _svc()
    expiries = yh.past_monthly_expiries(svc.now().date(), 3)
    svc.ticker_lists = lambda: {"LOW": ["Fuente A"], "RICH": ["Fuente A"], "ILLQ": ["Fuente A"], "BRK-B": ["Fuente A"]}
    svc.quality.save([TI(t, option_liquidity=liq) for t, liq in (("LOW", 3), ("RICH", 3), ("ILLQ", 1), ("BRK-B", 3))])
    closes = [(svc.now().date() - timedelta(days=d), 100.0) for d in range(0, 150)]
    for t in ("LOW", "RICH", "ILLQ"):
        svc.bars.upsert(t, closes)
    sym = lambda t, e: yh.option_symbol(t, e, 95.0)
    bids = {sym("LOW", e): 0.3 for e in expiries} | {sym("RICH", e): 2.0 for e in expiries} | {sym("ILLQ", e): 2.0 for e in expiries}
    provider = _FakeBidHistory(bids)
    svc.option_history = provider
    async def go():
        assert await svc.fetch_yield_history(5.0, 3, 2) == 6                 # 2 tickers (liquidez >= 2, sin clase de acciones) x 3 vencimientos
        assert await svc.fetch_yield_history(5.0, 3, 2) == 0                 # lo guardado no se vuelve a pedir
    import asyncio
    asyncio.run(go())
    assert len(provider.requests) == 1 and not any("ILLQ" in s or "BRK" in s for s in provider.requests[0])
    verdicts = {v.ticker: v for v in svc.yield_history_report(5.0, 1.0, 3, 2, 2, 2)}
    assert set(verdicts) == {"LOW", "RICH"}                                   # ILLQ cae por la liquidez; BRK-B no se consulta
    assert verdicts["LOW"].status == yh.DISCARD and abs(verdicts["LOW"].best_pct - 0.3 / 95 * 100) < 1e-9
    assert verdicts["RICH"].status == yh.OK and verdicts["RICH"].hits == 3
    assert svc.bid_history.count() == 6 and svc.yield_history_progress is None


def test_silent_contracts_are_retried_and_empty_ones_count_as_answered_without_bid():
    from scanner_opciones.scanner import yield_history as yh

    svc = _svc()
    expiries = yh.past_monthly_expiries(svc.now().date(), 2)
    svc.ticker_lists = lambda: {"ZZZ": ["Fuente A"]}
    svc.bars.upsert("ZZZ", [(svc.now().date() - timedelta(days=d), 100.0) for d in range(0, 150)])
    s1, s2 = (yh.option_symbol("ZZZ", e, 95.0) for e in expiries)
    provider = _FakeBidHistory({s1: 1.5}, silent=[s2])
    svc.option_history = provider
    import asyncio
    assert asyncio.run(svc.fetch_yield_history(5.0, 2, None)) == 1            # solo respondió uno: el otro se pedirá otra vez
    provider.silent = set()
    provider.empty = {s2}
    assert asyncio.run(svc.fetch_yield_history(5.0, 2, None)) == 1
    [v] = svc.yield_history_report(5.0, 1.0, 2, 1, 2, None)
    assert v.with_data == 1 and v.status == yh.OK                              # un mes llega; el otro respondió sin bid
    assert yh.judge("ZZZ", v.months, 1.0, 2, 2).status == yh.NO_DATA


def test_criba_page_shows_the_history_section_and_the_post_starts_the_request():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        page = client.get("/scanner/criba")
        assert page.status_code == 200 and "Histórico real de bids" in page.text and "Pedir los datos que faltan" in page.text
        assert 'name="hp"' in page.text and 'name="hl"' in page.text
        svc.option_history = None
        sent = client.post("/scanner/criba/historico", data={"hm": "3"}, follow_redirects=False)
        assert sent.status_code == 303 and "/scanner/criba" in sent.headers["location"] and "message=" in sent.headers["location"]


def test_history_progress_is_shown_and_refreshes_itself_and_the_result_or_error_is_kept():
    import asyncio
    from scanner_opciones.scanner import yield_history as yh

    svc = _svc()
    expiries = yh.past_monthly_expiries(svc.now().date(), 1)
    svc.ticker_lists = lambda: {"ZZZ": ["Fuente A"]}
    svc.bars.upsert("ZZZ", [(svc.now().date() - timedelta(days=d), 100.0) for d in range(0, 150)])
    seen = []

    class Provider(_FakeBidHistory):
        async def option_bid_history(self, symbols, days, on_progress=None):
            seen.append(svc.yield_history_progress)               # durante la petición ya hay un texto de avance
            return await super().option_bid_history(symbols, days, on_progress)

    svc.option_history = Provider({yh.option_symbol("ZZZ", expiries[0], 95.0): 1.5})
    asyncio.run(svc.fetch_yield_history(5.0, 1, None))
    assert seen and "contratos con respuesta" in seen[0] and svc.yield_history_progress is None
    assert svc.yield_history_result.startswith("Terminado: 1 contratos guardados")

    class Broken:
        async def option_bid_history(self, symbols, days, on_progress=None):
            raise RuntimeError("sin red")

    svc.bid_history.db.conn.execute("DELETE FROM option_bid_history")
    svc.option_history = Broken()
    assert asyncio.run(svc.fetch_yield_history(5.0, 1, None)) == 0
    assert svc.yield_history_result == "Error: sin red" and svc.yield_history_progress is None      # el error queda a la vista y no bloquea la siguiente petición

    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        page = client.get("/scanner/criba").text
        assert "Error: sin red" in page and "window.location.reload" not in page
        svc.yield_history_progress = "Pidiendo bids a tastytrade: 10/100 contratos con respuesta"
        page = client.get("/scanner/criba").text
        assert "En curso." in page and "10/100 contratos" in page and "window.location.reload" in page


class _FakeDetailHistory(_FakeBidHistory):
    """Además del bid, ask/OI de un símbolo (`detail`: {símbolo: (ask, OI)}); los símbolos sin entrada responden sin ninguna vela."""

    def __init__(self, bids, detail):
        super().__init__(bids)
        self.detail, self.detail_requests = detail, []

    async def option_detail_history(self, symbols, days, on_progress=None):
        self.detail_requests.append(list(symbols))
        out = {}
        for s in symbols:
            ask, oi = self.detail.get(s, (None, None))
            day = TODAY - timedelta(days=40)
            out[s] = {"ask": {d: ask for d in (TODAY - timedelta(days=n) for n in range(0, days)) if ask}, "last": {d: (oi, 0.4, 10) for d in (TODAY - timedelta(days=n) for n in range(0, days)) if oi is not None}}
        return out


def test_detail_is_requested_only_for_contracts_with_bid_and_filters_the_strikes_by_spread_and_oi():
    import asyncio
    from scanner_opciones.domain.models import TickerInfo as TI
    from scanner_opciones.scanner import yield_history as yh

    svc = _svc()
    expiries = yh.past_monthly_expiries(svc.now().date(), 2)
    svc.ticker_lists = lambda: {"WIDE": ["Fuente A"], "GOOD": ["Fuente A"], "NOBID": ["Fuente A"]}
    svc.quality.save([TI(t, option_liquidity=3) for t in ("WIDE", "GOOD", "NOBID")])
    for t in ("WIDE", "GOOD", "NOBID"):
        svc.bars.upsert(t, [(svc.now().date() - timedelta(days=d), 100.0) for d in range(0, 150)])
    sym = lambda t, e: yh.option_symbol(t, e, 95.0)
    bids = {sym(t, e): 2.0 for t in ("WIDE", "GOOD") for e in expiries}
    detail = {sym("WIDE", e): (4.0, 900) for e in expiries} | {sym("GOOD", e): (2.1, 900) for e in expiries}   # WIDE: spread 67 %; GOOD: 5 %
    provider = _FakeDetailHistory(bids, detail)
    svc.option_history = provider

    # sin filtros del perfil no se piden las velas de ask/last y el bid basta
    asyncio.run(svc.fetch_yield_history(5.0, 2, None))
    assert provider.detail_requests == []
    assert {v.ticker: v.status for v in svc.yield_history_report(5.0, 1.0, 2, 2, 2, None)} == {"WIDE": yh.OK, "GOOD": yh.OK, "NOBID": yh.NO_DATA}
    # con filtros y sin detalle pedido, el strike queda pendiente en vez de contar
    verdicts = {v.ticker: v for v in svc.yield_history_report(5.0, 1.0, 2, 2, 2, None, 25.0, 100)}
    assert verdicts["GOOD"].status == yh.PENDING

    asyncio.run(svc.fetch_yield_history(5.0, 2, None, True))
    assert len(provider.detail_requests) == 1 and not any("NOBID" in s for s in provider.detail_requests[0])    # sin bid no hay nada que medir
    assert len(provider.detail_requests[0]) == 4 and svc.yield_history_result.startswith("Terminado: 0 contratos guardados · ask/OI/IV de 4/4")
    asyncio.run(svc.fetch_yield_history(5.0, 2, None, True))
    assert len(provider.detail_requests) == 1 and svc.yield_history_result.startswith("No falta")                # lo ya guardado no se repite
    assert svc.bid_history.count() == 6                                                                         # el upsert no borró nada

    verdicts = {v.ticker: v for v in svc.yield_history_report(5.0, 1.0, 2, 2, 2, None, 25.0, 100)}
    assert verdicts["GOOD"].status == yh.OK and verdicts["GOOD"].months[0].illiquid == 0
    assert verdicts["WIDE"].status == yh.DISCARD and verdicts["WIDE"].best_pct == 0.0 and verdicts["WIDE"].months[0].illiquid == 1   # spread de 67 % > 25 %
    assert {v.ticker: v.status for v in svc.yield_history_report(5.0, 1.0, 2, 2, 2, None, None, 1000)}["GOOD"] == yh.DISCARD         # OI 900 < 1000


def test_strike_yield_treats_missing_data_like_the_scanner():
    from scanner_opciones.scanner import yield_history as yh

    ok = (2.0, 2.1, 500, 0.4, 10, True)
    assert yh.strike_yield(100, ok, None, None) == (2.0, False, False)
    assert yh.strike_yield(100, ok, 10.0, 100) == (2.0, False, False)
    assert yh.strike_yield(100, (2.0, None, 500, None, None, True), 10.0, None) == (None, False, True)        # sin ask no se conoce el spread
    assert yh.strike_yield(100, (2.0, 2.1, None, None, None, True), None, 100) == (None, False, True)         # sin OI no pasa
    assert yh.strike_yield(100, (2.0, 2.1, None, None, None, False), None, 100) == (None, True, False)        # detalle sin pedir: pendiente
    assert yh.strike_yield(100, (None, None, None, None, None, False), None, 100) == (None, False, False)     # sin bid: no hay mercado
    assert yh.strike_yield(100, None, None, None) == (None, True, False)
    assert yh.pick_last({}, __import__("datetime").date(2026, 1, 5)) is None


def test_migration_v43_keeps_the_stored_bids_and_marks_them_as_missing_detail():
    import sqlite3
    from datetime import date, datetime

    from scanner_opciones.storage import db as dbm

    d = dbm.Database(":memory:")
    d.conn.execute("DROP TABLE option_bid_history")
    d.conn.execute("CREATE TABLE option_bid_history (ticker TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL NOT NULL, ref_day TEXT NOT NULL, bid REAL, fetched_at TEXT NOT NULL, PRIMARY KEY (ticker, expiry, strike))")
    d.conn.execute("INSERT INTO option_bid_history VALUES ('AAA','2026-07-17',10.0,'2026-06-17',0.5,'x'), ('BBB','2026-07-17',10.0,'2026-06-17',NULL,'x')")
    d.conn.execute("ALTER TABLE daily_bars DROP COLUMN volume")      # la v44 (volumen diario) se vuelve a aplicar al migrar
    d.conn.execute("PRAGMA user_version = 42")
    d.migrate()
    repo = __import__("scanner_opciones.storage.repositories", fromlist=["BidHistoryRepo"]).BidHistoryRepo(d)
    assert repo.count() == 2 and repo.missing_detail() == [("AAA", date(2026, 7, 17), 10.0, date(2026, 6, 17))]
    repo.save_detail([("AAA", date(2026, 7, 17), 10.0, 0.6, 300, 0.4, 12)], datetime(2026, 11, 6))
    repo.save([("AAA", date(2026, 7, 17), 10.0, date(2026, 6, 17), 0.55)], datetime(2026, 11, 6))          # volver a guardar el bid no pierde el detalle
    assert repo.all_details()[("AAA", date(2026, 7, 17), 10.0)] == (0.55, 0.6, 300, 0.4, 12, True) and repo.missing_detail() == []


def test_criba_spread_is_the_profiles_or_the_configured_minimum_whichever_is_larger():
    from scanner_opciones.ui import routes_criba

    svc = _svc()
    floor = svc.settings.scanner.criba_max_spread_pct
    assert routes_criba._criba_spread(svc, None) is None                         # el perfil no filtra por spread: la criba tampoco
    assert routes_criba._criba_spread(svc, 20.0) == floor                        # el de cierre es más ancho: se sube al mínimo configurado
    assert routes_criba._criba_spread(svc, floor + 30) == floor + 30             # un perfil más permisivo manda


def _bars(svc, closes):
    """Cierres diarios de ZZZ que acaban ayer: `closes` en orden cronológico."""
    n = len(closes)
    svc.bars.upsert("ZZZ", [(TODAY - timedelta(days=n - i), px) for i, px in enumerate(closes)])


def test_technical_entry_and_moving_average_filters_apply_in_the_cds_show_chips_count_in_the_impact_and_are_saved():
    svc = _svc()
    with _client(svc) as client:
        client.portal.call(svc.wait_idle)
        _seed(svc)                                              # ZZZ cotiza a 100
        _bars(svc, [70 + i * 0.5 for i in range(60)])           # sube sin parar hasta ~100: precio sobre la MA50 y la MA50 asciende
        base = cds_profile_query(svc)
        text = client.get("/scanner?" + base).text
        for needle in ('id="sec-entry"', 'id="sec-ma"', 'name="use_rsi"', 'name="boll_pctb"', 'name="ma50"', 'name="ema20"', 'name="slope_ma50"', 'name="ma_frame"'):
            assert needle in text, needle
        assert '<td class="tk"' in text                         # sin filtros técnicos no cambia nada
        # precio sobre la EMA20 y la EMA20 sobre la MA50 en una tendencia alcista: pasa y sale el chip
        ok = client.get("/scanner?" + base + "&ema20=above&cmp_ema20_ma50=gte").text
        assert '<td class="tk"' in ok and "▲ EMA20 D" in ok and "EMA20 ≥ MA50 D" in ok
        # precio bajo la MA50: no pasa ningún spread
        bad = client.get("/scanner?" + base + "&ma50=below").text
        assert '<td class="tk"' not in bad and "▼ MA50 D" in bad
        # RSI que rebota (desde ≤ 35) en una subida continua: no hay rebote
        rsi = client.get("/scanner?" + base + "&use_rsi=on&rsi_max=35").text
        assert '<td class="tk"' not in rsi and "RSI rebota desde ≤ 35" in rsi
        # con 1 señal exigida de dos marcadas (RSI y precio cerca de la MA50 a ≤ 50 %) el ticker pasa
        near = client.get("/scanner?" + base + "&use_rsi=on&rsi_max=35&use_near=on&near_pct=50&signals=1").text
        assert '<td class="tk"' in near and "≥ 1 señales de entrada" in near
        # el panel de impacto cuenta cada grupo como un filtro más
        impact = client.get("/scanner/impact?" + base + "&ma50=below&use_rsi=on").text
        assert "Medias móviles / EMA" in impact and "Técnicos de entrada" in impact
        # valores fuera de rango o desconocidos son un error del formulario
        assert "Parámetro" in client.get("/scanner?" + base + "&ma50=sideways").text or "desconocido" in client.get("/scanner?" + base + "&ma50=sideways").text
        # se guardan en el perfil y vuelven al cargarlo
        client.post("/scanner/profile/save", data={"name": "CDS tecnico", "query": base + "&ema20=above&use_near=on&near_pct=8"})
        prof = next(p for p in svc.stored_profiles()[0] if p.name == "CDS tecnico")
        assert prof.cds["ema20"] == "above" and prof.cds["use_near"] == "on" and prof.cds["near_pct"] == "8"
        page = client.get("/scanner?mode=cds&profile=CDS+tecnico").text
        assert 'name="use_near" checked' in page and "▲ EMA20 D" in page
