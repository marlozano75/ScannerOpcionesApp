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
    long_put = Position("AAPL", 1, 50.0, "Technology", OptionContract("AAPL", pos.option.expiry, 95.0, OptionRight.PUT), 40.0)
    svc.state.positions = [pos, long_put]
    with pytest.raises(ValueError, match="spread"):
        await svc.roll_evaluation(put_key(pos))


def test_the_panel_shows_the_zone_and_the_roll_button_and_the_route_renders(client_and_service):
    client, svc, gw, _ = client_and_service
    pos = put_position()
    svc.state.positions = [pos]
    stage(gw)
    from scanner_opciones.scanner.profiles import Profile
    svc.save_profiles([Profile("Tasty", ("put",), management="tastytrade"), Profile("Tpc", ("put",), management="tpc")], "Tasty")
    svc.set_put_profile(put_key(pos), "Tasty")
    page = client.get("/").text
    assert 'class="roll-btn"' in page and "En el dinero" in page and "Evaluar roll" in page
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
    svc.state.positions = [pos, long_leg]                                              # ahora es un spread: no se cotiza
    assert await svc.refresh_position_deltas() is False and len(asked) == 2 and svc.state.position_deltas == {}


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
    assert "Delta estimada" in est and "~ ·" in est
    svc.state.position_deltas = {put_key(pos): 0.58}
    real = client.get("/").text
    assert "Delta real de tastytrade" in real and "Δ 0.58 ·" in real and "Δ 0.58~" not in real


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
