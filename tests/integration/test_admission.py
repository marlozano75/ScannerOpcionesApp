"""Admisión a la Watchlist: estilos TPC y Tastytrade, fuentes directas, tickers protegidos por posiciones y confirmación explícita."""
from datetime import date, datetime, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import Position, TickerInfo
from scanner_opciones.marketdata.candles import FakeCandles
from scanner_opciones.marketdata.volatility import FakeVolatility, IVMetrics
from scanner_opciones.storage.db import Database
from scanner_opciones.watchlist.parser import parse_tokens

NOW = datetime(2026, 10, 20, 12, 0)
TICKERS = ("DIRECT", "TPCOK", "TPCFLAT", "TASTYOK", "NOPE", "OLD", "HELD", "LOWQ")


def series(values):
    start = NOW.date() - timedelta(days=len(values))
    return [(start + timedelta(days=i), float(v)) for i, v in enumerate(values)]


UP_WITH_DIP = [100 + i * 0.1 for i in range(300)][:-1] + [121.0]          # sube, y cierra ~7 % por debajo de su máximo, aún sobre la MA200
FLAT_AT_HIGH = [130 - i * 0.1 for i in range(300)]                          # cae: cierre bajo la MA200 y esa media baja


def good(t, **kw):
    return TickerInfo(t, eps_ttm=2.0, positive_quarters=4, option_liquidity=3, debt_to_equity=0.2, **kw)


def make(tmp_path):
    gw = FakeGateway()
    gw.connected = True
    vol = FakeVolatility(metrics={"TASTYOK": IVMetrics(iv_rank=60, iv_percentile=55), "NOPE": IVMetrics(iv_rank=10, iv_percentile=12),
                                  "TPCOK": IVMetrics(iv_rank=15, iv_percentile=20)})
    candles = FakeCandles(bars={"TPCOK": series(UP_WITH_DIP), "TPCFLAT": series(FLAT_AT_HIGH)})
    svc = AppService(gw, Database(tmp_path / "app.db"), Settings(), lambda: NOW, volatility=vol, candles=candles)
    for t in TICKERS:
        svc._has_options[t] = True
    return svc, vol, candles


async def prepare(svc):
    await svc.add_manual_tickers(parse_tokens(["DIRECT"]), "TastyHourly")                       # fuente directa de la configuración
    await svc.add_manual_tickers(parse_tokens(["TPCOK", "TPCFLAT", "TASTYOK", "NOPE", "LOWQ", "OLD", "HELD"]), "MisCosas")
    svc.quality.save([good("TPCOK"), good("TPCFLAT"), TickerInfo("TASTYOK", option_liquidity=3), good("NOPE"),
                      TickerInfo("LOWQ", eps_ttm=-1.0, positive_quarters=1, option_liquidity=3), good("OLD"), good("HELD")])
    svc.watchlist.add(["OLD", "HELD"], NOW)
    svc.state.positions = [Position("HELD", 100, 1000.0)]


async def run(svc):
    assert svc.admission_start()
    await svc.wait_idle()
    return svc.admission_view()


async def test_each_style_and_the_direct_sources_decide_what_passes(tmp_path):
    svc, vol, candles = make(tmp_path)
    await prepare(svc)
    report = await run(svc)
    by = {v.ticker: v for v in report.verdicts}
    assert by["DIRECT"].styles == ["Directa"] and by["DIRECT"].passes                      # la fuente directa pasa sin comprobar nada
    assert by["TPCOK"].styles == ["TPC"] and by["TPCOK"].tasty                              # calidad + tendencia; su IV es baja
    assert by["TPCFLAT"].tpc and "tendencia" in by["TPCFLAT"].tpc                           # sin tendencia alcista
    assert by["TASTYOK"].styles == ["Tastytrade"] and by["TASTYOK"].tpc                     # IV Rank alto y liquidez; sin calidad para TPC
    assert not by["NOPE"].passes and not by["LOWQ"].passes and "beneficios" in by["LOWQ"].tpc
    assert {v.ticker for v in report.to_add} == {"DIRECT", "TPCOK", "TASTYOK"}              # solo lo que no estaba en la Watchlist
    assert {v.ticker for v in report.to_drop} == {"OLD", "HELD"}
    assert by["HELD"].protected and not by["OLD"].protected
    assert "Beneficios" in by["LOWQ"].tpc_all and not by["TPCOK"].tpc_all and by["TPCOK"].tasty_all == ("IV bajo",)
    assert by["TPCFLAT"].tpc_all and "Tendencia" in by["TPCFLAT"].tpc_all
    rows = report.by_source()
    assert sum(r.total for r in rows) == sum(len(v.sources) for v in report.verdicts)              # una fila por fuente; un ticker cuenta en cada una
    for r in rows:
        in_src = [v for v in report.verdicts if r.name in v.sources]
        assert r.passing == sum(v.passes for v in in_src) and r.in_watchlist == sum(v.in_watchlist for v in in_src)
        assert r.passing_in_watchlist == sum(v.passes and v.in_watchlist for v in in_src)
        assert sum(r.tpc.values()) == sum(len(v.tpc_all) for v in in_src if not v.direct)


async def test_closes_are_only_downloaded_for_candidates_that_pass_the_quality_gate(tmp_path):
    svc, vol, candles = make(tmp_path)
    await prepare(svc)
    await run(svc)
    asked = {t for call in candles.calls for t in call}
    assert asked == {"TPCOK", "TPCFLAT", "NOPE", "OLD", "HELD"}                                         # ni los directos ni los que fallan la calidad
    assert "DIRECT" not in {t for call in vol.calls for t in call}                                       # tampoco se pide su IV
    await run(svc)
    assert len(candles.calls) == 1                                                                       # la segunda evaluación reutiliza los cierres


async def test_apply_adds_only_what_the_report_allows_and_remove_never_touches_positions(tmp_path):
    svc, _, _ = make(tmp_path)
    await prepare(svc)
    await run(svc)
    new = await svc.admission_apply(["TPCOK", "NOPE", "DIRECT"])                              # NOPE no pasa: se ignora
    assert sorted(new) == ["DIRECT", "TPCOK"]
    await svc.wait_idle()
    assert {"TPCOK", "DIRECT"} <= set(svc.watchlist.list()) and "NOPE" not in svc.watchlist.list()
    removed, protected = svc.admission_remove(["OLD", "HELD", "TPCOK"])                         # TPCOK cumple: no se quita
    assert removed == ["OLD"] and protected == ["HELD"]
    assert "OLD" not in svc.watchlist.list() and {"HELD", "TPCOK"} <= set(svc.watchlist.list())
    assert not svc.admission_view().to_add or "TPCOK" not in {v.ticker for v in svc.admission_view().to_add}


async def test_a_source_can_be_marked_as_direct_and_the_config_ones_cannot_be_unmarked(tmp_path):
    svc, _, _ = make(tmp_path)
    await prepare(svc)
    assert "MisCosas" not in svc.direct_source_names()
    svc.set_direct_source("MisCosas", True)
    assert "MisCosas" in svc.direct_source_names()
    report = await run(svc)
    assert {v.ticker for v in report.verdicts if v.direct} >= {"TPCFLAT", "NOPE", "LOWQ"}
    svc.set_direct_source("MisCosas", False)
    svc.set_direct_source("TastyHourly", False)                                                  # la de la configuración sigue
    assert "MisCosas" not in svc.direct_source_names() and "TastyHourly" in svc.direct_source_names()


async def test_nothing_is_evaluated_twice_at_once_and_a_provider_failure_is_reported(tmp_path):
    svc, vol, candles = make(tmp_path)
    await prepare(svc)
    candles.error = RuntimeError("DXLink caído")
    report = await run(svc)
    assert any("cierres" in n for n in report.notes)                                             # avisa de que no pudo medir la tendencia
    assert {v.ticker for v in report.verdicts if v.tpc is None} == set()                          # sin cierres, nadie cumple TPC


def test_admission_page_renders_before_and_after_an_evaluation(tmp_path):
    import asyncio
    from fastapi.testclient import TestClient
    from scanner_opciones.ui.web import create_app

    async def go():
        svc, _, _ = make(tmp_path)
        await prepare(svc)
        return svc

    svc = asyncio.run(go())
    client = TestClient(create_app(svc))
    page = client.get("/admission")
    assert page.status_code == 200 and "Todavía no se ha evaluado" in page.text and "TastyHourly" in page.text
    src = client.get("/universe?src=MisCosas")
    assert src.status_code == 200 and "Marcar como fuente directa" in src.text
    hourly = client.get("/universe?src=TastyHourly")
    assert "Fuente directa" in hourly.text and "admission.direct_sources" in hourly.text
    asyncio.run(_evaluate(svc))
    done = client.get("/admission").text
    assert "pasarían a la Watchlist" in done and "TPCOK" in done and "EN CARTERA" in done and "Quitar los marcados" in done and "Por fuente" in done


async def _evaluate(svc):
    await run(svc)
