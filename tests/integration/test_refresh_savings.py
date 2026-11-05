"""Ahorro del refresco (F0 de docs/seleccion-tickers.md): tope de márgenes por ciclo y catálogo limitado por los modos."""


from scanner_opciones.domain.models import OptionQuote
from scanner_opciones.scanner.modes import ALL_ON, TickerModes
from tests.integration.test_jobs import NOW, TODAY, Env, _prepare_refresh


def _refresh_settings(env, **kw):
    env.settings = env.settings.model_copy(update={"refresh": env.settings.refresh.model_copy(update=kw)})
    env.refresh.settings = env.settings


async def test_margin_cap_defers_the_rest_to_the_next_cycle_and_asks_the_best_yield_first():
    env = Env()
    c75, c80 = await _prepare_refresh(env)
    env.gw.quotes[c80] = OptionQuote(bid=1.0, ask=1.2, last=1.1, delta=-0.1, iv=0.3, open_interest=300)
    env.gw.margins[c80] = 1200.0
    _refresh_settings(env, margin_max_per_cycle=1)
    r = await env.refresh.run()
    assert (r.margins_requested, r.margins_deferred) == (1, 1)
    by = {s.contract.strike: s for s in env.snaps.all("AAPL")}
    assert by[75.0].initial_margin == 1500.0 and by[80.0].initial_margin is None       # el de 75 $ rinde más: va primero
    r = await env.refresh.run()                                                         # el de 75 $ se reutiliza, entra el pendiente
    assert (r.margins_requested, r.margins_reused, r.margins_deferred) == (1, 1, 0)
    assert {s.contract.strike: s.initial_margin for s in env.snaps.all("AAPL")}[80.0] == 1200.0


async def test_margin_cap_zero_means_no_limit():
    env = Env()
    c75, c80 = await _prepare_refresh(env)
    env.gw.quotes[c80] = OptionQuote(bid=1.0, ask=1.2, open_interest=300)
    env.gw.margins[c80] = 1200.0
    _refresh_settings(env, margin_max_per_cycle=0)
    r = await env.refresh.run()
    assert (r.margins_requested, r.margins_deferred) == (2, 0)


async def test_catalog_does_not_add_contracts_for_a_ticker_without_the_mode_that_uses_them():
    env = Env()
    env.add_aapl()
    env.daily.syncer.modes_provider = lambda: {"AAPL": TickerModes(put=False, put_spread=False, cds=False)}
    await env.daily.run_pending()
    assert env.contracts.list("AAPL") == []                                  # sin modo no se estrenan contratos
    env.daily.syncer.modes_provider = lambda: {"AAPL": ALL_ON}
    await env.daily.syncer.sync("AAPL", await env.daily.syncer.chain("AAPL", TODAY), 100.0, TODAY)
    assert [c.strike for c in env.contracts.list("AAPL")] == [75.0, 80.0]


async def test_catalog_keeps_what_is_already_stored_when_the_mode_turns_off():
    env = Env()
    env.add_aapl()
    await env.daily.run_pending()
    assert len(env.contracts.list("AAPL")) == 2
    env.daily.syncer.modes_provider = lambda: {"AAPL": TickerModes(put=False, put_spread=False, cds=False)}
    await env.daily.syncer.sync("AAPL", await env.daily.syncer.chain("AAPL", TODAY), 100.0, TODAY)
    assert len(env.contracts.list("AAPL")) == 2                              # un modo que oscila no obliga a cualificar de nuevo


