"""Admisión: reglas por fuente (directa / filtros que aplica), interruptor «sin filtros» y periodicidad de las fuentes API/MCP."""
import asyncio
from datetime import datetime

from fastapi.testclient import TestClient

from scanner_opciones.scanner.admission import SourceRule
from scanner_opciones.storage.repositories import MetaRepo
from scanner_opciones.tortuga.schedule import FeedSchedule, ScheduleError, load_schedule, parse_times, save_schedule
from scanner_opciones.ui.web import create_app

from tests.integration.test_admission import make, prepare, run

import pytest


async def test_a_source_without_filters_lets_everything_through_and_a_filter_can_be_removed(tmp_path):
    svc, _, _ = make(tmp_path)
    await prepare(svc)
    svc.set_admission_rules({"MisCosas": SourceRule(direct=False, tpc=frozenset(), tasty=frozenset())})
    by = {v.ticker: v for v in (await run(svc)).verdicts}
    assert all(by[t].passes and by[t].via == ("Sin filtros",) for t in ("NOPE", "LOWQ", "TPCFLAT"))       # la fuente pasa entera
    svc.set_admission_rules({"MisCosas": SourceRule(tpc=frozenset({"liquidity"}), tasty=frozenset())})     # solo la liquidez de TPC
    report = await run(svc)
    by = {v.ticker: v for v in report.verdicts}
    assert by["LOWQ"].passes and by["LOWQ"].via == ("TPC",)                  # sus beneficios negativos ya no cuentan
    assert by["TPCFLAT"].passes and by["NOPE"].passes
    assert by["LOWQ"].tpc is None and by["LOWQ"].tasty == "este estilo no se aplica a sus fuentes"
    row = next(r for r in report.by_source() if r.name == "MisCosas")
    assert row.passing == row.total and set(row.tpc) <= {"Liquidez"}          # los filtros quitados no cuentan como rechazo


async def test_direct_can_be_changed_per_source_even_for_the_config_ones(tmp_path):
    svc, _, _ = make(tmp_path)
    await prepare(svc)
    assert svc.admission_rule("TastyHourly").direct and not svc.admission_rule("MisCosas").direct
    svc.set_admission_rules({"TastyHourly": SourceRule(direct=False), "MisCosas": SourceRule(direct=True)})
    assert "MisCosas" in svc.direct_source_names() and "TastyHourly" not in svc.direct_source_names()
    by = {v.ticker: v for v in (await run(svc)).verdicts}
    assert by["DIRECT"].via != ("Directa",) and by["NOPE"].via == ("Directa",)
    svc.reset_admission_rules()
    assert svc.admission_rule("TastyHourly").direct and "MisCosas" not in svc.direct_source_names()


async def test_removing_all_filters_lets_the_whole_universe_through_without_measuring(tmp_path):
    svc, vol, candles = make(tmp_path)
    await prepare(svc)
    svc.set_admission_filters_off(True)
    report = await run(svc)
    assert report.filters_off and all(v.passes for v in report.verdicts)
    assert {v.ticker for v in report.to_add} == {"DIRECT", "TPCOK", "TPCFLAT", "TASTYOK", "NOPE", "LOWQ"} and not report.to_drop
    assert not candles.calls and not vol.calls                                # no se descarga nada
    svc.set_admission_filters_off(False)
    assert not (await run(svc)).filters_off


def test_schedule_runs_in_market_out_of_market_at_fixed_times_and_after_the_close(tmp_path):
    from zoneinfo import ZoneInfo
    from scanner_opciones.config.settings import Settings
    from scanner_opciones.market.hours import MarketCalendar
    ny = ZoneInfo("America/New_York")
    market = MarketCalendar.from_settings(Settings().market)
    sch = FeedSchedule(in_market=30, out_market=120, times=("08:00",), after_close=False)
    open_ = datetime(2026, 10, 19, 10, 0, tzinfo=ny)                                        # lunes, abierto
    assert sch.is_due(open_, market, None, None, 60)
    last = datetime(2026, 10, 19, 9, 45, tzinfo=ny)
    assert not sch.is_due(open_, market, last, last, 60) and sch.is_due(open_.replace(hour=10, minute=20), market, last, last, 60)
    closed = datetime(2026, 10, 19, 18, 0, tzinfo=ny)                                       # cerrado, día hábil
    assert not sch.is_due(closed, market, closed.replace(hour=17), closed.replace(hour=17), 60)
    assert sch.is_due(closed.replace(hour=19, minute=10), market, closed.replace(hour=17), closed.replace(hour=17), 60)    # pasadas 2 h
    weekend = datetime(2026, 10, 24, 12, 0, tzinfo=ny)
    assert not sch.is_due(weekend, market, None, None, 60)                                  # sábado: nada
    off = FeedSchedule()
    assert not off.is_due(open_, market, None, None, 60)                                    # sin ninguna modalidad: solo a mano


def test_schedule_validates_and_persists(tmp_path):
    from scanner_opciones.config.settings import Settings
    from scanner_opciones.storage.db import Database
    meta = MetaRepo(Database(tmp_path / "s.db"))
    settings = Settings()
    assert load_schedule(settings, meta, "radar_spreads") == FeedSchedule(in_market=30, after_close=True)          # lo de config.yaml
    assert load_schedule(settings, meta, "comunidad").times == ("11:15",)
    save_schedule(meta, "radar_spreads", FeedSchedule(in_market=10, times=("09:30", "21:00")))
    assert load_schedule(settings, meta, "radar_spreads") == FeedSchedule(in_market=10, times=("09:30", "21:00"))
    with pytest.raises(ScheduleError):
        save_schedule(meta, "tasty_lists", FeedSchedule(in_market=1))                        # las listas no bajan de 5 minutos
    with pytest.raises(ScheduleError):
        parse_times("25:00")
    assert parse_times("11:5, 09:30 9:30") == ("09:05", "09:30") or parse_times("11:5, 09:30 9:30") == ("09:30", "11:05")


def test_admission_page_edits_rules_off_switch_and_schedule(tmp_path):
    async def go():
        svc, _, _ = make(tmp_path)
        await prepare(svc)
        return svc

    svc = asyncio.run(go())
    client = TestClient(create_app(svc), follow_redirects=False)
    page = client.get("/admission").text
    assert "Reglas por fuente" in page and "Periodicidad de las fuentes" in page and "Quitar todos los filtros" in page
    names = [s.name for s in svc.universe_sources]
    i = names.index("MisCosas")
    form = {"src": names, f"tpc_{i}": ["liquidity"], f"direct_{names.index('TastyHourly')}": "1"}
    assert client.post("/admission/rules", data=form).status_code == 303
    rule = svc.admission_rule("MisCosas")
    assert rule.tpc == frozenset({"liquidity"}) and not rule.tasty and not rule.direct and svc.admission_rule("TastyHourly").direct
    assert client.post("/admission/rules/reset").status_code == 303 and svc.admission_rule("MisCosas") == SourceRule()
    client.post("/admission/off", data={"off": "1"})
    assert svc.admission_filters_off() and "quitados" in client.get("/admission").text
    client.post("/admission/off", data={"off": "0"})
    assert not svc.admission_filters_off()
    client.post("/admission/schedule", data={"in_radar_spreads": "15", "times_comunidad": "10:00, 22:30", "close_comunidad": "1"})
    assert svc.feed_schedule("radar_spreads") == FeedSchedule(in_market=15)
    assert svc.feed_schedule("comunidad") == FeedSchedule(times=("10:00", "22:30"), after_close=True)
    bad = client.post("/admission/schedule", data={"times_comunidad": "99:99"})
    assert bad.status_code == 303 and svc.feed_schedule("comunidad").times == ("10:00", "22:30")      # no se guarda nada si algo no vale
    client.post("/admission/schedule/reset")
    assert svc.feed_schedule("radar_spreads") == FeedSchedule(in_market=30, after_close=True)


async def test_a_source_can_require_that_its_tickers_have_options(tmp_path):
    svc, vol, _ = make(tmp_path)
    await prepare(svc)
    svc.manual_sources["Sueltos"] = ["NOOPT", "TPCOK"]                        # NOOPT quedó sin comprobar al entrar: tastytrade no lo conoce
    assert "NOOPT" not in svc._has_options
    svc.set_admission_rules({"Sueltos": SourceRule(direct=True)})              # directa, pero con la comprobación de opciones (por defecto)
    report = await run(svc)
    by = {v.ticker: v for v in report.verdicts}
    assert not by["NOOPT"].passes and by["NOOPT"].no_options and by["NOOPT"].tpc == "sin contratos de opciones"
    assert by["TPCOK"].passes                                                  # tiene opciones
    row = next(r for r in report.by_source() if r.name == "Sueltos")
    assert row.no_options == 1 and row.passing == 1
    assert svc._has_options["NOOPT"] is False                                  # el resultado se recuerda y el Universo lo descarta en adelante
    svc.manual_sources["Libres"] = ["NOOPT2"]
    svc.set_admission_rules({"Libres": SourceRule(direct=True, options=False)})        # sin comprobar las opciones, pasa directo
    by = {v.ticker: v for v in (await run(svc)).verdicts}
    assert by["NOOPT2"].passes and by["NOOPT2"].via == ("Directa",)
