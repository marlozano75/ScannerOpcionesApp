"""Margen estimado (fórmula Reg T calibrada) para el Scanner; el what-if real solo para las puts vendidas en cartera."""
import pytest

from tests.integration.test_jobs import Env, NOW, _prepare_refresh

from scanner_opciones.domain.models import OptionContract, OptionQuote
from scanner_opciones.metrics import margin_estimate as me


def _what_if_spy(env):
    asked = []
    real = env.gw.what_if_margin

    async def spy(contract, quantity=1):
        asked.append(contract)
        return await real(contract, quantity)
    env.gw.what_if_margin = spy
    return asked


async def test_without_positions_nothing_is_asked_to_tws_and_the_margin_is_estimated():
    env = Env()
    env.refresh.estimate_margins = True
    c75, c80 = await _prepare_refresh(env)
    asked = _what_if_spy(env)
    report = await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    assert asked == [] and report.margins_requested == 0 and report.margins_estimated == 1
    assert s.initial_margin == pytest.approx(me.reg_t_formula(100.0, 75.0, 1.1))   # (1,1 + máx(20 − 0, 7,5)) × 100
    assert s.margin_at is None                                                      # estimado: no cuenta como real


async def test_a_short_put_in_the_portfolio_still_gets_the_real_what_if():
    env = Env()
    env.refresh.estimate_margins = True
    c75, c80 = await _prepare_refresh(env)
    env.refresh.owned_puts = lambda: {("AAPL", c75.expiry, 75.0)}
    asked = _what_if_spy(env)
    await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    assert asked == [c75] and s.initial_margin == 1500.0 and s.margin_at == NOW


async def test_estimate_is_calibrated_with_the_real_margin_of_the_same_ticker():
    env = Env()
    env.refresh.estimate_margins = True
    c75, c80 = await _prepare_refresh(env)
    env.refresh.estimate_margins = False
    await env.refresh.run()                                   # what-if real: 1.500 (la fórmula daría 2.110)
    env.refresh.estimate_margins = True
    env.clock = NOW.replace(hour=20)                          # pasa el tiempo: el real caduca y ahora se estima
    env.refresh.owned_puts = None
    await env.refresh.run()
    s = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0]
    ratio = 1500.0 / me.reg_t_formula(100.0, 75.0, 1.1)
    assert s.initial_margin == pytest.approx(me.reg_t_formula(100.0, 75.0, 1.1) * ratio) and s.margin_at is None


def test_ratios_ignore_estimates_and_persist_for_tickers_without_new_samples():
    c = OptionContract("AAPL", NOW.date(), 75.0)
    from scanner_opciones.domain.models import ContractSnapshot
    real = ContractSnapshot(contract=c, updated_at=NOW, bid=1.0, ask=1.2, initial_margin=1500.0, margin_at=NOW, margin_price=100.0)
    est = ContractSnapshot(contract=OptionContract("KO", NOW.date(), 50.0), updated_at=NOW, bid=1.0, ask=1.2, initial_margin=9999.0, margin_price=100.0)
    r = me.MarginRatios.calibrate([real, est], default=1.0)
    assert set(r.per_ticker) == {"AAPL"}
    merged = r.updated_with(me.MarginRatios.calibrate([est], default=1.0))   # sin muestras reales nuevas
    assert merged.per_ticker == r.per_ticker and merged.overall == r.overall
    assert me.MarginRatios.from_json(merged.to_json()).per_ticker == r.per_ticker
    assert me.MarginRatios.from_json("basura", 1.3).for_ticker("X") == 1.3


async def test_estimate_is_reused_until_the_price_moves_beyond_the_tolerance():
    env = Env()
    env.refresh.estimate_margins = True
    c75, c80 = await _prepare_refresh(env)
    await env.refresh.run()
    first = {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0].initial_margin
    env.gw.quotes[c75] = OptionQuote(bid=1.5, ask=1.7, open_interest=300)    # la prima cambia, el precio no
    report = await env.refresh.run()
    assert report.margins_estimated == 0 and {x.contract.strike: x for x in env.snaps.all("AAPL")}[75.0].initial_margin == first
    env.gw.prices["AAPL"] = 90.0                                              # -10 %: supera la tolerancia
    report = await env.refresh.run()
    assert report.margins_estimated == 1
