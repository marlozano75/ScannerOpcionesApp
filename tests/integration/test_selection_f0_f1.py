"""Selección de tickers, fases F0 y F1 (docs/seleccion-tickers.md): tope de márgenes por ciclo, catálogo limitado por los modos
y registro diario de prima y liquidez de cierre."""
from datetime import timedelta

import pytest

from scanner_opciones.domain.models import OptionQuote
from scanner_opciones.scanner.modes import ALL_ON, TickerModes
from scanner_opciones.storage.repositories import PremiumRepo
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


async def test_premium_history_records_best_yield_and_closing_liquidity_per_profile():
    env = Env()
    c75, c80 = await _prepare_refresh(env)
    env.refresh.premium_repo = repo = PremiumRepo(env.db)
    await env.refresh.run()
    rows = {r["profile"]: r for r in repo.history("AAPL")}
    assert set(rows) == {"Inicial", "Estrategia"}
    r = rows["Inicial"]
    assert r["day"] == TODAY.isoformat() and r["contracts"] == 2
    assert r["closable"] == 1                                                # el de 80 $: spread 40 % y OI 50 → no se recompra fácil
    assert r["best_yield_pct"] == pytest.approx((1.0 + 0.25 * 0.2) / 75 * 100)
    assert r["best_closable_yield_pct"] == pytest.approx(r["best_yield_pct"])
    assert r["max_oi"] == 300 and r["best_ann_wide_pct"] == pytest.approx(r["best_yield_pct"] * 365 / 30)
    await env.refresh.run()
    assert len(repo.history("AAPL", "Inicial")) == 1                         # una fila por ticker, perfil y día


async def test_premium_history_is_skipped_for_partial_runs():
    env = Env()
    await _prepare_refresh(env)
    env.refresh.premium_repo = repo = PremiumRepo(env.db)
    await env.refresh.run(tickers={"AAPL"})
    assert repo.history("AAPL") == []


async def test_warm_tickers_are_quoted_once_a_day():
    env = Env()
    await _prepare_refresh(env)
    env.refresh.warm_tickers = lambda: {"AAPL"}
    first = await env.refresh.run()
    assert first.in_scope == 2 and first.warm_skipped == 0                  # aún sin cotizar hoy: se cotiza
    second = await env.refresh.run()
    assert second.in_scope == 0 and second.warm_skipped == 2                # ya cotizado hoy: este ciclo se salta
    env.clock = NOW + timedelta(days=1)
    third = await env.refresh.run()
    assert third.in_scope == 2                                              # al día siguiente vuelve a tocar
    env.refresh.warm_tickers = None
    assert (await env.refresh.run()).in_scope == 2                          # sin selección, todos los ciclos
