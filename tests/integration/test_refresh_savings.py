"""Ahorro del refresco (F0 de docs/seleccion-tickers.md): catálogo limitado por los modos."""


from scanner_opciones.domain.models import OptionQuote
from scanner_opciones.scanner.modes import ALL_ON, TickerModes
from tests.integration.test_jobs import NOW, TODAY, Env, _prepare_refresh


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


