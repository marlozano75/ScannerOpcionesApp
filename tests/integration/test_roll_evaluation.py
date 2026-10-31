"""Evaluación de rolls con cotizaciones en vivo (AppService.roll_evaluation, GET /management/roll) y avisos de defensa en el Panel."""
from datetime import timedelta

import pytest

from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionChain, OptionContract, OptionQuote, Position
from scanner_opciones.portfolio.management import put_key
from tests.integration.test_service import NOW, TODAY, started_service
from tests.integration.test_web import client_and_service  # noqa: F401


def put_position(strike=100.0, days=14, qty=-1, mark=3.0, credit=2.0):
    o = OptionContract("AAPL", TODAY + timedelta(days=days), strike, OptionRight.PUT)
    return Position("AAPL", qty, -mark * 100 * abs(qty), "Technology", o, credit * 100)


def stage(gw, ask=3.1):
    """Cadena y cotizaciones: la put actual (100, a 14 días) y candidatas a 45 y 80 días."""
    e45, e80 = TODAY + timedelta(days=45), TODAY + timedelta(days=80)
    gw.chains["AAPL"] = OptionChain("AAPL", [TODAY + timedelta(days=14), e45, e80], [90.0, 92.5, 95.0, 97.5, 100.0, 105.0, 108.0, 110.0])
    cur = OptionContract("AAPL", TODAY + timedelta(days=14), 100.0, OptionRight.PUT)
    gw.quotes[cur] = OptionQuote(bid=2.9, ask=ask, delta=-0.52)
    for strike, bid, delta in [(100.0, 4.6, -0.50), (97.5, 4.0, -0.44), (95.0, 3.6, -0.38), (92.5, 3.0, -0.31), (90.0, 2.5, -0.26)]:
        gw.quotes[OptionContract("AAPL", e45, strike, OptionRight.PUT)] = OptionQuote(bid=bid, ask=bid + 0.1, delta=delta)
    gw.quotes[OptionContract("AAPL", e80, 95.0, OptionRight.PUT)] = OptionQuote(bid=4.4, ask=4.6, delta=-0.35)
    e14 = TODAY + timedelta(days=14)
    for strike, ask in [(95.0, 1.5), (92.5, 0.9), (90.0, 0.6)]:                                  # puts de protección (mismo vencimiento)
        gw.quotes[OptionContract("AAPL", e14, strike, OptionRight.PUT)] = OptionQuote(bid=ask - 0.1, ask=ask, delta=-0.2)
    for strike, bid, delta in [(105.0, 1.6, 0.27), (108.0, 0.9, 0.21), (110.0, 0.5, 0.15)]:      # calls del strangle (mismo vencimiento)
        gw.quotes[OptionContract("AAPL", e14, strike, OptionRight.CALL)] = OptionQuote(bid=bid, ask=bid + 0.1, delta=delta)


async def test_roll_evaluation_finds_the_lowest_strike_with_net_credit():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    ev = await svc.roll_evaluation(put_key(pos))
    assert ev.close_live and ev.close_cost == pytest.approx(3.1) and ev.entry == pytest.approx(2.0) and ev.delta == pytest.approx(0.52)
    assert ev.defense.zone == "atm" and ev.checked > ev.quoted > 0
    assert ev.report.verdict == "roll"
    # crédito mínimo = max(0,05; 10 % de 2,00) = 0,20 sobre el coste de cerrar (3,10): 95 cobra 3,60 − 3,10 = 0,50; 92,5 cobra 3,00 → débito
    best45 = next(o for o in ev.report.best if o.dte == 45)
    assert best45.strike == 95.0 and best45.net_credit == pytest.approx(0.50) and best45.ok and best45.delta == pytest.approx(0.38)
    assert all(o.strike <= 100.0 for o in ev.report.best)


async def test_roll_evaluation_without_credit_says_to_close_or_take_the_assignment():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw, ask=6.0)                                       # cerrar cuesta tanto que ningún roll cobra crédito
    ev = await svc.roll_evaluation(put_key(pos))
    assert ev.report.verdict == "none" and "no es viable" in ev.report.text


async def test_roll_evaluation_flags_earnings_in_the_new_expiry_and_rejects_spreads_and_missing_puts():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    from scanner_opciones.domain.models import TickerInfo
    svc.quality.save([TickerInfo("AAPL", next_earnings=TODAY + timedelta(days=30))])
    ev = await svc.roll_evaluation(put_key(pos))
    assert all(any("resultados" in n for n in o.notes) for o in ev.report.best)       # los resultados caen entre la put actual y los nuevos vencimientos
    with pytest.raises(ValueError, match="ya no está"):
        await svc.roll_evaluation("MSFT|2026-11-20|100")


def test_the_panel_shows_the_zone_and_the_roll_button_and_the_route_renders(client_and_service):
    client, svc, gw, _ = client_and_service
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    from scanner_opciones.scanner.profiles import Profile
    svc.save_profiles([Profile("Tasty", ("put",), management="tastytrade"), Profile("Tpc", ("put",), management="tpc")], "Tasty")
    svc.set_put_profile(put_key(pos), "Tasty")
    page = client.get("/").text
    assert 'class="roll-btn"' in page and "En el dinero" in page and "↻</a>" in page
    frag = client.get("/management/roll", params={"key": put_key(pos)}).text
    assert "Roll viable" in frag and "Crédito neto" in frag and "95" in frag and "<html" not in frag
    assert "ya no está en la cartera" in client.get("/management/roll", params={"key": "MSFT|2026-11-20|100"}).text
    svc.set_put_profile(put_key(pos), "Tpc")                                              # TPC no rola: ni zona ni botón
    assert 'class="roll-btn"' not in client.get("/").text


# ---- delta real de las puts de la cartera -------------------------------------------------------------------------
async def test_position_deltas_come_from_the_provider_and_are_reused_while_fresh():
    svc, gw = await started_service()
    pos = put_position()
    long_leg = Position("AAPL", 1, 50.0, "Technology", OptionContract("AAPL", TODAY + timedelta(days=14), 90.0, OptionRight.PUT), 40.0)
    other = Position("MSFT", -1, -100.0, "Technology", OptionContract("MSFT", TODAY + timedelta(days=14), 300.0, OptionRight.PUT), 150.0)
    svc.state.positions = [pos, other]
    asked = []
    original = gw.get_quotes

    async def spy(contracts):
        asked.append(list(contracts))
        return await original(contracts)
    gw.get_quotes = spy
    stage(gw)
    gw.quotes[OptionContract("MSFT", TODAY + timedelta(days=14), 300.0, OptionRight.PUT)] = OptionQuote(bid=1.4, ask=1.6, delta=-0.21)
    assert await svc.refresh_position_deltas() is True
    assert svc.state.position_deltas == {put_key(pos): pytest.approx(0.52), put_key(other): pytest.approx(0.21)}
    assert len(asked) == 1 and len(asked[0]) == 2
    assert await svc.refresh_position_deltas() is False and len(asked) == 1           # reciente: no se vuelve a pedir
    assert await svc.refresh_position_deltas(force=True) is True and len(asked) == 2
    svc.state.positions = [pos, long_leg]                                              # ahora es un spread: se cotiza su pata vendida (misma clave)
    assert await svc.refresh_position_deltas() is False and len(asked) == 2 and svc.state.position_deltas == {put_key(pos): pytest.approx(0.52)}   # y MSFT ya no está
    assert await svc.refresh_position_deltas(force=True) is True and [c.strike for c in asked[-1]] == [100.0]     # el spread solo cotiza su vendida


async def test_a_failing_quote_keeps_the_previous_deltas_and_the_panel_falls_back_to_the_estimate():
    from scanner_opciones.domain.errors import DataUnavailableError
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    assert await svc.refresh_position_deltas() is True
    before = dict(svc.state.position_deltas)

    async def boom(contracts):
        raise DataUnavailableError("sin proveedor")
    gw.get_quotes = boom
    assert await svc.refresh_position_deltas(force=True) is False and svc.state.position_deltas == before


def test_the_panel_marks_a_real_delta_and_an_estimated_one(client_and_service):
    client, svc, gw, _ = client_and_service
    pos = put_position()
    svc.state.positions = [pos]
    from scanner_opciones.scanner.profiles import Profile
    svc.save_profiles([Profile("Tasty", ("put",), management="tastytrade")], "Tasty")
    svc.set_put_profile(put_key(pos), "Tasty")
    est = client.get("/").text
    assert "Delta: estimada" in est and __import__("re").search(r"Δ\d\.\d\d~ ", est)
    svc.state.position_deltas = {put_key(pos): 0.58}
    real = client.get("/").text
    assert "Delta: real de tastytrade" in real and "Δ0.58 " in real and "Δ0.58~" not in real


async def test_roll_evaluation_offers_concrete_protection_puts_and_calls():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    ev = await svc.roll_evaluation(put_key(pos))
    # protección: banda 90–95 (5–10 % por debajo de 100); crédito original 2,00 → débito máximo 1,00: 92,5 (0,90) y 90 (0,60) cumplen, 95 (1,50) no
    assert [p.strike for p in ev.protections] == [92.5, 90.0, 95.0] and [p.ok for p in ev.protections] == [True, True, False]
    assert ev.protections[0].max_loss == pytest.approx(7.5 - (2.0 - 0.9))
    # calls: Δ 0,20–0,30 → 105 (0,27) y 108 (0,21); la de Δ 0,15 no entra; prima ≥ 0,20 (10 % de 2,00)
    assert [c.strike for c in ev.calls] == [105.0, 108.0] and ev.calls_quoted == 3 and all(c.ok for c in ev.calls)
    assert ev.calls[0].breakeven_down == pytest.approx(100.0 - (2.0 + 1.6)) and ev.calls[0].breakeven_up == pytest.approx(105.0 + 3.6)


async def test_a_call_failure_does_not_stop_the_roll_and_protection():
    from scanner_opciones.domain.errors import DataUnavailableError
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    original = gw.get_quotes

    async def no_calls(contracts):
        if any(c.right is OptionRight.CALL for c in contracts):
            raise DataUnavailableError("el proveedor no lista calls")
        return await original(contracts)
    gw.get_quotes = no_calls
    ev = await svc.roll_evaluation(put_key(pos))
    assert ev.calls == () and ev.calls_quoted == 0 and ev.report.verdict == "roll" and ev.protections


def test_the_roll_window_shows_the_call_and_protection_tables(client_and_service):
    client, svc, gw, _ = client_and_service
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    frag = client.get("/management/roll", params={"key": put_key(pos)}).text
    assert "Vender la call (strangle)" in frag and "Breakeven abajo" in frag and "105" in frag
    assert "Comprar protección (put comprada)" in frag and "Pérdida máx." in frag and "demasiado cara" in frag


# ---- spreads ------------------------------------------------------------------------------------------------------
def spread_positions(days=14):
    """Put spread 100/95 (ancho 5) a 14 días: vendida a 2,50 y comprada a 1,00 → crédito neto 1,50; hoy vale 2,20 (vendida 3,20, comprada 1,00)."""
    exp = TODAY + timedelta(days=days)
    short = Position("AAPL", -1, -320.0, "Technology", OptionContract("AAPL", exp, 100.0, OptionRight.PUT), 250.0)
    long = Position("AAPL", 1, 100.0, "Technology", OptionContract("AAPL", exp, 95.0, OptionRight.PUT), 100.0)
    return short, long


def stage_spread(gw):
    e14, e55 = TODAY + timedelta(days=14), TODAY + timedelta(days=40)
    gw.chains["AAPL"] = OptionChain("AAPL", [e14, e55], [85.0, 87.5, 90.0, 92.5, 95.0, 97.5, 100.0, 105.0, 108.0, 110.0, 113.0])
    P, C = OptionRight.PUT, OptionRight.CALL
    gw.quotes[OptionContract("AAPL", e14, 100.0, P)] = OptionQuote(bid=3.0, ask=3.2, delta=-0.52)
    gw.quotes[OptionContract("AAPL", e14, 95.0, P)] = OptionQuote(bid=1.0, ask=1.1, delta=-0.30)          # cerrar: 3,20 − 1,00 = 2,20
    # roll a 40 días, mismo ancho: (vendida, comprada) con crédito del nuevo spread = bid vendida − ask comprada
    for k, sb, lk, la, d in [(100.0, 5.0, 95.0, 2.4, -0.50), (97.5, 4.4, 92.5, 2.0, -0.44), (95.0, 3.8, 90.0, 1.6, -0.38)]:
        gw.quotes[OptionContract("AAPL", e55, k, P)] = OptionQuote(bid=sb, ask=sb + 0.1, delta=d)
        gw.quotes[OptionContract("AAPL", e55, lk, P)] = OptionQuote(bid=la - 0.1, ask=la, delta=-0.3)
    for k, bid, d in [(105.0, 1.0, 0.27), (108.0, 0.6, 0.21)]:                                              # call vendida del call spread
        gw.quotes[OptionContract("AAPL", e14, k, C)] = OptionQuote(bid=bid, ask=bid + 0.1, delta=d)
    for k, ask in [(110.0, 0.3), (113.0, 0.2)]:                                                             # call comprada, a +ancho
        gw.quotes[OptionContract("AAPL", e14, k, C)] = OptionQuote(bid=ask - 0.05, ask=ask, delta=0.1)


async def test_spread_evaluation_rolls_the_whole_spread_with_net_credit_and_offers_call_spreads():
    svc, gw = await started_service()
    short, long = spread_positions()
    svc.state.positions = [short, long]
    stage_spread(gw)
    ev = await svc.roll_evaluation(put_key(short))
    assert ev.kind == "spread" and ev.long_strike == 95.0 and ev.close_live and ev.close_cost == pytest.approx(2.2) and ev.entry == pytest.approx(1.5)
    assert ev.delta == pytest.approx(0.52) and ev.defense.zone == "atm" and ev.defense.delta_live
    assert ev.report.verdict == "roll" and len(ev.report.best) == 1
    best = ev.report.best[0]                                  # necesita 0,20 sobre 2,20: 100/95 cobra 2,60 (0,40 pero Δ 0,50), 97,5/92,5 cobra 2,40 (0,20)
    assert (best.strike, best.long_strike, best.dte) == (97.5, 92.5, 40) and best.net_credit == pytest.approx(0.20) and best.ok
    assert [(c.short_strike, c.long_strike) for c in ev.call_spreads] == [(105.0, 110.0), (108.0, 113.0)]
    assert ev.call_spreads[0].credit == pytest.approx(0.7) and ev.call_spreads[0].ok and ev.call_spreads[0].new_credit == pytest.approx(2.2)
    assert ev.protections == () and ev.calls == ()            # un spread ya está protegido: no hay strangle ni put de protección


async def test_spread_evaluation_without_credit_says_not_to_roll():
    svc, gw = await started_service()
    short, long = spread_positions()
    svc.state.positions = [short, long]
    stage_spread(gw)
    gw.quotes[OptionContract("AAPL", TODAY + timedelta(days=14), 100.0, OptionRight.PUT)] = OptionQuote(bid=5.0, ask=5.4, delta=-0.7)    # cerrar cuesta 4,30
    ev = await svc.roll_evaluation(put_key(short))
    assert ev.report.verdict == "none" and "no rueles" in ev.report.text


def test_the_panel_offers_the_defense_of_a_spread_and_the_window_renders(client_and_service):
    client, svc, gw, _ = client_and_service
    short, long = spread_positions()
    svc.state.positions = [short, long]
    stage_spread(gw)
    from scanner_opciones.scanner.profiles import Profile
    svc.save_profiles([Profile("Tasty", ("spread",), management="tastytrade")], "Tasty")
    svc.set_put_profile(put_key(short), "Tasty")
    svc.state.position_deltas = {put_key(short): 0.55}              # delta real de la vendida (sin ella el precio igual al strike sale «cómodo»)
    page = client.get("/").text
    assert 'class="roll-btn"' in page and "↻</a>" in page and "rola fuera YA con crédito" in page
    frag = client.get("/management/roll", params={"key": put_key(short)}).text
    assert "spread 100.00/95.00" in frag and "Vendida/comprada" in frag and "97.50/92.50" in frag
    assert "Añadir un call spread (iron condor)" in frag and "Riesgo máx." in frag and "Vender la call (strangle)" not in frag


# ---- histórico de deltas: se guarda con el precio y solo se reutiliza si sigue valiendo -----------------------------
def _set_spot(svc, price):
    from dataclasses import replace
    info = svc.ticker_info.all()["AAPL"]
    svc.ticker_info.upsert(replace(info, underlying_price=price))


def _forget_memory(svc):
    """Como tras un reinicio: nada de deltas en memoria."""
    svc.state.position_deltas, svc.state.position_delta_meta, svc.state.position_deltas_at = {}, {}, None
    svc._position_delta_keys = set()


async def test_position_deltas_are_logged_with_the_underlying_price_and_restored_after_a_restart():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    _set_spot(svc, 100.0)
    assert await svc.refresh_position_deltas() is True
    assert svc.position_delta_log.latest([put_key(pos)])[put_key(pos)][1:] == (pytest.approx(0.52), 100.0)
    _forget_memory(svc)
    asked = []
    original = gw.get_quotes

    async def spy(contracts):
        asked.append(contracts)
        return await original(contracts)
    gw.get_quotes = spy
    await svc.refresh_position_deltas()
    assert svc.state.position_deltas == {put_key(pos): pytest.approx(0.52)} and not asked      # recuperada del disco: no se vuelve a pedir


async def test_a_logged_delta_is_not_restored_when_the_price_has_moved():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    _set_spot(svc, 100.0)
    await svc.refresh_position_deltas()
    _forget_memory(svc)
    _set_spot(svc, 103.0)                                      # +3 % > greeks_max_move_pct
    gw.quotes[OptionContract("AAPL", TODAY + timedelta(days=14), 100.0, OptionRight.PUT)] = OptionQuote(bid=2.0, ask=2.2, delta=-0.40)
    assert await svc.refresh_position_deltas() is True
    assert svc.state.position_deltas == {put_key(pos): pytest.approx(0.40)}                  # se pidió de nuevo en vez de usar la guardada


async def test_a_moved_price_makes_the_panel_ignore_the_delta_and_ask_again():
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    _set_spot(svc, 100.0)
    await svc.refresh_position_deltas()
    assert svc.usable_position_deltas() == {put_key(pos): pytest.approx(0.52)}
    _set_spot(svc, 102.0)
    assert svc.usable_position_deltas() == {}                 # el Panel estimará con el precio actual
    assert await svc.refresh_position_deltas() is True        # y aunque sea reciente se vuelve a cotizar
    _set_spot(svc, 102.5)                                       # la nueva medida es de 102: un 0,5 % no la invalida
    assert svc.usable_position_deltas() != {}


async def test_put_assignments_prefer_the_profiles_that_showed_the_contract_in_the_scan_log():
    from scanner_opciones.scanner.profiles import Profile
    svc, gw = await started_service()
    pos = put_position()                                           # AAPL 100, 14 DTE, abierta "hoy" (primera vez vista)
    svc.state.positions = [pos]
    a = Profile("A", ("put",), {"discount": "0", "dte_min": "10", "dte_max": "20", "min_yield": "0"}, {})       # por filtros ganaría A (rango estrecho)
    b = Profile("B", ("put",), {"discount": "0", "dte_min": "1", "dte_max": "60", "min_yield": "0"}, {})
    svc.save_profiles([a, b], "A")
    svc._note_put_opened()
    with svc.scan_log_repo.db.conn as conn:
        conn.execute("INSERT INTO scan_log (profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, active) "
                     "VALUES ('B','put','AAPL',?,100.0,NULL,?,?,1)", ((TODAY + timedelta(days=14)).isoformat(), NOW.isoformat(), NOW.isoformat()))
    assert svc.put_assignments()[put_key(pos)] == "B"
    assert "registro de escaneos" in svc.put_assignment_notes()[put_key(pos)]


def _feed_row(svc, profile, trader, opened, origin="Comunidad"):
    with svc.scan_log_repo.db.conn as conn:
        conn.execute("INSERT INTO scan_log (profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, active, origin, trader, opened) "
                     "VALUES (?,'put','AAPL',?,100.0,NULL,?,?,1,?,?,?)",
                     (profile, (TODAY + timedelta(days=14)).isoformat(), NOW.isoformat(), NOW.isoformat(), origin, trader, opened))


async def test_put_assignments_use_the_feed_history_with_the_trader_when_the_scan_log_says_nothing():
    from scanner_opciones.scanner.profiles import Profile
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    a = Profile("A", ("put",), {"discount": "0", "dte_min": "10", "dte_max": "20", "min_yield": "0"}, {})       # por filtros ganaría A (rango estrecho)
    b = Profile("B", ("put",), {"discount": "0", "dte_min": "1", "dte_max": "60", "min_yield": "0"}, {})
    svc.save_profiles([a, b], "A")
    svc._note_put_opened()
    key = put_key(pos)
    _feed_row(svc, "B", "Ana", TODAY.isoformat())
    _feed_row(svc, "Sin perfil", "Ana", TODAY.isoformat())                         # «Sin perfil» de la fuente no cuenta
    _feed_row(svc, "A", "Beto", (TODAY - timedelta(days=3)).isoformat())          # otro día de apertura: otro contrato
    assert svc.put_assignments()[key] == "B"
    note = svc.put_assignment_notes()[key]
    assert "histórico de Comunidad" in note and "la abrió Ana" in note and "encaja en B" in note
    assert svc.put_assignments()[key] == "B"                                      # definitiva: no cambia aunque cambien los registros
    _feed_row(svc, "A", "Ana", TODAY.isoformat())
    assert svc.put_assignments()[key] == "B"


async def test_the_scan_log_goes_before_the_feed_and_a_filter_based_fit_is_upgraded_when_the_history_arrives():
    from scanner_opciones.scanner.profiles import Profile
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    a = Profile("A", ("put",), {"discount": "0", "dte_min": "10", "dte_max": "20", "min_yield": "0"}, {})
    b = Profile("B", ("put",), {"discount": "0", "dte_min": "1", "dte_max": "60", "min_yield": "0"}, {})
    svc.save_profiles([a, b], "A")
    svc._note_put_opened()
    key = put_key(pos)
    assert svc.put_assignments()[key] == "A" and "filtros al abrir" in svc.put_assignment_notes()[key]      # sin registros: filtros
    assert svc.put_assignments()[key] == "A"
    _feed_row(svc, "B", "Ana", TODAY.isoformat())                                  # la comunidad publica al día siguiente: llega la prueba
    assert svc.put_assignments()[key] == "B" and "la abrió Ana" in svc.put_assignment_notes()[key]
    svc.meta.set("put_entry_fit", "{}")
    with svc.scan_log_repo.db.conn as conn:                                        # el registro de escaneos manda sobre la fuente
        conn.execute("INSERT INTO scan_log (profile, kind, ticker, expiry, strike, long_strike, first_seen, last_seen, active) "
                     "VALUES ('A','put','AAPL',?,100.0,NULL,?,?,1)", ((TODAY + timedelta(days=14)).isoformat(), NOW.isoformat(), NOW.isoformat()))
    assert svc.put_assignments()[key] == "A" and "registro de escaneos" in svc.put_assignment_notes()[key]


async def test_the_feed_history_matches_a_contract_the_trader_opened_a_few_days_before_you():
    from scanner_opciones.config.settings import CommunitySettings
    from scanner_opciones.scanner.profiles import Profile
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    a = Profile("A", ("put",), {"discount": "0", "dte_min": "10", "dte_max": "20", "min_yield": "0"}, {})
    b = Profile("B", ("put",), {"discount": "0", "dte_min": "1", "dte_max": "60", "min_yield": "0"}, {})
    svc.save_profiles([a, b], "A")
    svc._note_put_opened()
    key = put_key(pos)
    _feed_row(svc, "B", "Ana", (TODAY - timedelta(days=2)).isoformat())          # el trader la abrió dos días antes que tú
    object.__setattr__(svc.settings, "community", CommunitySettings(feed_match_days=0))
    assert svc.put_assignments()[key] == "A"                                       # sin ventana no coincide: por filtros
    object.__setattr__(svc.settings, "community", CommunitySettings(feed_match_days=3))
    assert svc.put_assignments()[key] == "B"                                       # con ventana de 3 días sí (reemplaza la de filtros)
    assert "la abrió Ana el" in svc.put_assignment_notes()[key] and ", tú el" in svc.put_assignment_notes()[key]


async def test_your_own_trades_in_the_community_feed_go_before_those_of_other_traders():
    from scanner_opciones.scanner.profiles import Profile
    svc, gw = await started_service()
    pos = put_position()
    svc.state.positions = [pos]
    a = Profile("A", ("put",), {"discount": "0", "dte_min": "10", "dte_max": "20", "min_yield": "0"}, {})
    b = Profile("B", ("put",), {"discount": "0", "dte_min": "1", "dte_max": "60", "min_yield": "0"}, {})
    svc.save_profiles([a, b], "A")
    svc._note_put_opened()
    key = put_key(pos)
    _feed_row(svc, "B", "Ana", TODAY.isoformat())                                  # otro trader, el mismo día
    _feed_row(svc, "A", "Marloz", (TODAY - timedelta(days=1)).isoformat())         # tu operación (mayúsculas distintas), publicada con otra fecha
    assert svc.put_assignments()[key] == "A"                                       # manda la tuya, aunque la de Ana esté más cerca
    note = svc.put_assignment_notes()[key]
    assert "tu propia operación (marloz)" in note and "Ana" not in note and "abierta aquí el" in note
