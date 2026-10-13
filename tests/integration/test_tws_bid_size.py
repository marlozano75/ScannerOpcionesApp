"""El bid size de tastytrade es mucho menor que el de TWS: el refresco pide a TWS el de los contratos que pasan los demás filtros."""
from datetime import timedelta

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import OptionQuote
from tests.integration.test_jobs import NOW, Env, _prepare_refresh  # noqa: F401
from tests.integration.test_jobs import env  # noqa: F401


async def prepared(env, size=5, tws=120):
    c75, c80 = await _prepare_refresh(env)
    env.gw.quotes[c75] = OptionQuote(bid=1.0, ask=1.2, last=1.1, delta=-0.1, iv=0.3, open_interest=300, bid_size=size)
    env.gw.quotes[c80] = OptionQuote(bid=0.2, ask=0.3, open_interest=50, bid_size=size)     # yield < 1 %: no pasa el escaneo
    env.gw.tws_bid_sizes = True
    env.gw.bid_sizes[c75] = tws
    env.gw.bid_sizes[c80] = tws
    return c75, c80


def sizes(env):
    return {s.contract.strike: s for s in env.snaps.all("AAPL")}


async def test_small_sizes_of_contracts_that_pass_the_other_filters_are_replaced_by_the_tws_ones(env):
    c75, c80 = await prepared(env)
    report = await env.refresh.run()
    snaps = sizes(env)
    assert snaps[75.0].bid_size == 120 and snaps[75.0].bid_size_tws_at == NOW
    assert snaps[80.0].bid_size == 5 and snaps[80.0].bid_size_tws_at is None       # no pasa el yield: no se pide
    assert report.bid_sizes_checked == 1 and report.bid_sizes_changed == 1
    assert env.gw.bid_size_requests == [[c75]]


async def test_big_enough_sizes_are_not_requested(env):
    await prepared(env, size=50)
    report = await env.refresh.run()
    assert report.bid_sizes_checked == 0 and env.gw.bid_size_requests == [] and sizes(env)[75.0].bid_size == 50


async def test_a_recent_tws_size_is_kept_and_an_old_one_is_requested_again(env):
    c75, _ = await prepared(env)
    await env.refresh.run()
    env.clock = NOW + timedelta(minutes=5)
    report = await env.refresh.run()                     # el de TWS tiene 5 min: se reutiliza sin pedirlo otra vez
    assert len(env.gw.bid_size_requests) == 1 and report.bid_sizes_checked == 0
    assert sizes(env)[75.0].bid_size == 120 and sizes(env)[75.0].bid_size_tws_at == NOW
    env.gw.bid_sizes[c75] = 300
    env.clock = NOW + timedelta(minutes=20)
    await env.refresh.run()                              # caducado: el nuevo tamaño del proveedor lo pisa y se vuelve a pedir
    assert len(env.gw.bid_size_requests) == 2 and sizes(env)[75.0].bid_size == 300


async def test_it_can_be_switched_off_or_does_nothing_without_a_hybrid_gateway(env):
    await prepared(env)
    env.settings = Settings.model_validate({"refresh": {"tws_bid_size": False}})
    env.refresh.settings = env.settings
    await env.refresh.run()
    assert env.gw.bid_size_requests == [] and sizes(env)[75.0].bid_size == 5
    env.settings = Settings()
    env.refresh.settings = env.settings
    env.gw.tws_bid_sizes = False                         # p. ej. con `source: ibkr`: ya trae los tamaños de TWS
    await env.refresh.run()
    assert env.gw.bid_size_requests == []


async def test_the_cap_per_cycle_keeps_the_highest_annualized_yield(env):
    await prepared(env)
    env.settings = Settings.model_validate({"refresh": {"tws_bid_size_max_contracts": 1}})
    env.refresh.settings = env.settings
    env.gw.quotes[next(c for c in env.contracts.list("AAPL") if c.strike == 80.0)] = OptionQuote(
        bid=1.6, ask=1.8, delta=-0.1, iv=0.3, open_interest=300, bid_size=4)     # también pasa y rinde más (1,7 / 80 > 1,1 / 75)
    await env.refresh.run()
    assert [c.strike for r in env.gw.bid_size_requests for c in r] == [80.0]
