"""Tortuga desde la aplicación: radar de Bull Put Spreads, rutinas que lanzan la CLI de Claude (simulada) y fuente RadarSpreads."""
import json
import os
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import MetaRepo
from scanner_opciones.tortuga.claude_runner import ClaudeRunError, ToolCall, extract_json, parse_tool_calls
from scanner_opciones.tortuga.radar import RadarError, build_radar_document, parse_radar, passes
from scanner_opciones.tortuga.routines import RoutineRunner
from tests.integration.test_web import client_and_service  # noqa: F401

NOW = datetime(2026, 10, 19, 12, 0)


def row(symbol, strike=440.0, mid=15.0, sector="Energy", **over):
    base = {"symbol": symbol, "sector": sector, "price": 476.65, "change_pct": -0.06, "iv30_pct": 52.7, "iv_rank": 97.3, "iv_percentile": 98.8,
            "rv20_pct": 41.8, "vrp_pp": 10.9, "iv_hv": 1.26, "term_structure": "backwardation", "expected_move_30d_pct": 16.3, "rsi14": 74.4,
            "trend": "bullish", "earnings": {"date": "2026-11-03", "days_to": 18, "before_target_expiry": True}, "zone": "sell_premium",
            "top_strategy": {"key": "bullPutSpread", "name": "Bull Put Spread", "score": 52, "signal": "neutral"}, "signal": "neutral",
            "liquidity": {"stars": 2, "spread_pct": 7.4, "option_volume": 988, "volume_ratio_20": 0.63, "total_oi": 130066, "put_call_oi": 1.23, "put_call_volume": 0.54},
            "levels": {"put_wall": 430, "call_wall": 510, "max_pain": 430},
            "put30": {"strike": strike, "mid": mid, "delta": -0.28, "annualized_yield_pct": 35.6, "expiry": "2026-11-20", "dte": 35}}
    base.update(over)
    return base


def doc(rows, matched=None, **over):
    return {"version": 1, "generated_at": "2026-10-17T13:00:00+02:00", "as_of": "2026-10-16", "matched": len(rows) if matched is None else matched,
            "returned": len(rows), "rows": rows, **over}


def test_radar_rows_are_parsed_with_every_column_and_the_period_yield():
    data = parse_radar(doc([row("MPC"), row("XYZ", strike=100, mid=0.5)]))
    r = data.rows[0]
    assert r.ticker == "MPC" and r.iv_rank == 97.3 and r.put_strike == 440 and r.liquidity_stars == 2 and r.put_wall == 430
    assert r.earnings_before_expiry is True and r.strategy == "Bull Put Spread" and str(r.put_expiry) == "2026-11-20"
    assert round(r.put_yield_period_pct, 2) == 3.41 and round(r.distance_pct, 1) == 7.7
    assert passes(r, 1.0) and not passes(data.rows[1], 1.0)
    assert passes(data.rows[1], 0.5) and not passes(data.rows[1], 0.51)          # 0,5 / 100 = 0,5 % del strike


def test_missing_fields_stay_empty_and_never_pass():
    data = parse_radar(doc([{"symbol": "ABC"}]))
    assert data.rows[0].put_yield_period_pct is None and not passes(data.rows[0], 0.0)


def test_a_truncated_or_malformed_radar_is_rejected():
    for bad in (doc([row("A")], matched=59), doc([row("A")], returned=3), {"version": 2, "rows": []}, [], doc([row("A")], generated_at="ayer")):
        with pytest.raises(RadarError):
            parse_radar(bad)
    assert len(parse_radar(doc([row(f"T{i}") for i in range(130)])).rows) == 130           # varias llamadas por cuadrante pueden sumar más de 100


def test_extract_json_ignores_the_text_around_it():
    assert json.loads(extract_json('Aquí tienes:\n{"a": {"b": 1}}\nListo')) == {"a": {"b": 1}}
    with pytest.raises(ClaudeRunError):
        extract_json("FAILED")


def zone_calls(rows_by_zone, total=None, puts=None, **over):
    """Las llamadas a get_market_radar como las devuelve la CLI: la de recuento y una por tramo de precio."""
    every = [r for rows in rows_by_zone.values() for r in rows]
    n = len({r["symbol"] for r in every}) if total is None else total
    out = [ToolCall("get_market_radar", {"strategy": "bull_put_spread", "limit": 1},
                    json.dumps({"as_of": "2026-10-16", "matched": n, "returned": 1, "truncated": True, "rows": every[:1]}))]
    for zone, rows in rows_by_zone.items():
        body = {"as_of": "2026-10-16", "matched": len(rows), "returned": len(rows), "truncated": False, "rows": rows, **over}
        out.append(ToolCall("get_market_radar", {"strategy": "bull_put_spread", "price_min": 0, "price_max": 20, "limit": 100, "tag": zone}, json.dumps(body)))
    puts = puts or []
    out.append(ToolCall("get_market_radar", {"strategy": "short_put", "sort": "iv_rank", "limit": 100},
                        json.dumps({"as_of": "2026-10-16", "matched": len(puts), "returned": len(puts), "truncated": False, "rows": puts})))
    return out


def test_radar_document_is_built_from_the_raw_tool_results_of_the_four_zones():
    calls = zone_calls({"sell_premium": [row("MPC", iv_rank=97), row("PSX", iv_rank=94)], "caution": [row("ALL", iv_rank=85), row("MPC", iv_rank=97)]})
    d = build_radar_document(calls, NOW)
    assert [r["symbol"] for r in d["rows"]] == ["MPC", "PSX", "ALL"] and d["matched"] == d["returned"] == 3         # sin duplicados, por IV Rank
    assert len(parse_radar(d).rows) == 3
    with pytest.raises(RadarError, match="debería haber 5"):
        build_radar_document(zone_calls({"sell_premium": [row("A")]}, total=5), NOW)             # faltan valores respecto al recuento
    with pytest.raises(RadarError, match="recuento"):
        build_radar_document(calls[1:], NOW)
    with pytest.raises(RadarError, match="recortada"):
        build_radar_document(zone_calls({"sell_premium": [row("A")]}, total=59, matched=59), NOW)    # un tramo recortado que nadie completó


def call(args, rows, matched=None):
    body = {"as_of": "2026-10-16", "matched": len(rows) if matched is None else matched, "rows": rows}
    return ToolCall("get_market_radar", {"strategy": "bull_put_spread", "sort": "iv_rank", "limit": 100, **args}, json.dumps(body))


def empty_puts():
    return call({"strategy": "short_put"}, [])


def put_row(symbol, **over):
    return row(symbol, top_strategy={"key": "ventaPut", "name": "Venta de PUT", "score": 75, "signal": "favorable"}, **over)


def test_radar_needs_a_single_call_when_it_fits_and_splits_only_the_cut_ranges():
    rows = [row(f"T{i}", iv_rank=90 - i) for i in range(6)]
    assert len(build_radar_document([call({}, rows), empty_puts()], NOW)["rows"]) == 6                           # una sola llamada sin filtros, completa
    cut = call({}, rows[:3], matched=6)                                                             # recortada: 3 de 6
    halves = [call({"price_min": 0, "price_max": 50}, rows[:4]), call({"price_min": 50}, rows[4:])]
    assert [r["symbol"] for r in build_radar_document([cut, *halves, empty_puts()], NOW)["rows"]] == ["T0", "T1", "T2", "T3", "T4", "T5"]
    with pytest.raises(RadarError, match="debería haber 6"):
        build_radar_document([cut, halves[0], empty_puts()], NOW)                                                 # falta el tramo de 50 $ para arriba


def test_tool_calls_are_read_from_the_stream_without_the_model_copying_anything():
    prefix = "mcp__claude_ai_Tortuga_Trades__"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "ToolSearch", "input": {}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": prefix + "get_market_radar", "input": {"zone": "caution"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": '{"rows": []}'}]}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
        {"type": "result", "result": "DONE"},
    ]
    calls = parse_tool_calls("\n".join(json.dumps(m) for m in lines) + "\nno es json")
    assert calls == [ToolCall("get_market_radar", {"zone": "caution"}, '{"rows": []}')]


def make(tmp_path, run=None, run_calls=None, **tortuga):
    settings = Settings.model_validate({
        "community": {"path": str(tmp_path / "tortuga.json"), "openings_path": str(tmp_path / "aperturas.json")},
        "tortuga": {"radar_path": str(tmp_path / "radar.json"), "radar_puts_path": str(tmp_path / "radar_puts.json"), "prompts_dir": str(tmp_path / "prompts"), **tortuga}})
    (tmp_path / "prompts").mkdir(exist_ok=True)
    for name in ("aperturas_prompt.md", "comunidad_prompt.md", "radar_spreads_prompt.md", "radar_puts_prompt.md"):
        (tmp_path / "prompts" / name).write_text("prompt", encoding="utf-8")
    db = Database(tmp_path / "app.db")
    clock = {"now": NOW}
    runner = RoutineRunner(settings, MetaRepo(db), lambda: clock["now"], run or fake_claude({}), tmp_path, run_calls or fake_calls([]))
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, db, settings, lambda: clock["now"], tortuga=runner)
    for t in ("MPC", "PSX", "XYZ"):
        svc._has_options[t] = True
    return svc, runner, clock


def fake_calls(calls):
    async def run(command, prompt, tools, model, max_turns, timeout):
        return list(calls)

    return run


def fake_claude(answers):
    calls = []

    async def run(command, prompt, tools, model, max_turns, timeout):
        calls.append((tools, model))
        for key, text in answers.items():
            if key in tools or key == "*":
                return text() if callable(text) else text
        raise ClaudeRunError("sin respuesta simulada")

    run.calls = calls
    return run


async def test_routine_writes_the_file_only_when_the_answer_is_valid(tmp_path):
    svc, runner, clock = make(tmp_path, run_calls=fake_calls(zone_calls({"sell_premium": [row("MPC"), row("PSX")]})))
    ok, summary = await runner.run_one("radar_spreads")
    assert ok and "2 valores" in summary and (tmp_path / "radar.json").exists()
    before = (tmp_path / "radar.json").read_text(encoding="utf-8")
    bad = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: NOW, fake_claude({}), tmp_path,
                        fake_calls(zone_calls({"sell_premium": [row("A")]}, total=59)))
    ok, why = await bad.run_one("radar_spreads")
    assert not ok and "recortada" in why and (tmp_path / "radar.json").read_text(encoding="utf-8") == before        # se conserva el bueno
    assert bad.state()["radar_spreads"]["error"] == why


async def test_due_routines_follow_the_schedule_and_back_off_after_a_failure(tmp_path):
    svc, runner, clock = make(tmp_path, fake_claude({}), retry_minutes=60)
    clock["now"] = datetime(2026, 10, 19, 10, 0)
    def daily():
        return [r.key for r in runner.due() if r.key not in ("radar_spreads", "radar_puts")]            # el radar va por intervalo, no por hora

    assert daily() == []                                                            # antes de la primera hora (11:10)
    clock["now"] = datetime(2026, 10, 19, 11, 20)
    assert daily() == ["aperturas", "comunidad"]
    await runner.run_one("aperturas")                                               # falla: no hay respuesta simulada
    assert daily() == ["comunidad"]                           # no se reintenta enseguida
    clock["now"] = datetime(2026, 10, 19, 12, 30)
    assert "aperturas" in [r.key for r in runner.due()]                             # pasada la hora de espera, sí

async def test_radar_repeats_only_while_the_market_is_open_plus_one_pass_for_the_last_close(tmp_path):
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    svc, _, clock = make(tmp_path, retry_minutes=60)
    runner = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: clock["now"], fake_claude({}), tmp_path,
                           fake_calls(zone_calls({"sell_premium": [row("MPC")]})))

    def radar_due():
        return "radar_spreads" in [r.key for r in runner.due()]

    clock["now"] = datetime(2026, 10, 19, 8, 0, tzinfo=ny)                          # lunes antes de la apertura, nunca se leyó
    assert radar_due()                                                              # una pasada con la foto del último cierre
    await runner.run_one("radar_spreads")
    assert not radar_due()                                                          # y hasta que abra el mercado no se repite
    clock["now"] = datetime(2026, 10, 19, 10, 0, tzinfo=ny)
    assert radar_due()                                                              # abierto: toca, la última foto es anterior...
    await runner.run_one("radar_spreads")
    clock["now"] += timedelta(minutes=29)
    assert not radar_due()
    clock["now"] += timedelta(minutes=2)
    assert radar_due()                                                              # ...y se repite cada 30 minutos
    clock["now"] = datetime(2026, 10, 19, 15, 50, tzinfo=ny)
    await runner.run_one("radar_spreads")
    clock["now"] = datetime(2026, 10, 19, 16, 5, tzinfo=ny)                         # cerrado: la última pasada fue antes del cierre
    assert radar_due()
    await runner.run_one("radar_spreads")
    clock["now"] = datetime(2026, 10, 19, 23, 0, tzinfo=ny)
    assert not radar_due()                                                          # ya tiene la foto del cierre
    clock["now"] = datetime(2026, 10, 20, 8, 0, tzinfo=ny)
    assert not radar_due()                                                          # ni al día siguiente antes de abrir
    clock["now"] = datetime(2026, 10, 24, 12, 0, tzinfo=ny)
    assert radar_due()                                                              # el sábado el último cierre es el del viernes: falta su foto
    await runner.run_one("radar_spreads")
    clock["now"] = datetime(2026, 10, 25, 12, 0, tzinfo=ny)
    assert not radar_due()                                                          # y el fin de semana no se repite


async def test_the_openings_routine_stores_closes_too(tmp_path):
    text = json.dumps({"version": 1, "generated_at": "2026-10-17T13:00:00+02:00", "puts": [],
                       "closes": [{"date": "2026-10-15", "ticker": "VZ", "expiry": "2026-11-20", "strike": 40, "pct_captured": 62.2}]})
    svc, runner, _ = make(tmp_path, fake_claude({"list_trades": text}))
    ok, summary = await runner.run_one("aperturas")
    assert ok and "1 cierres" in summary and svc.openings().closes[0].ticker == "VZ"


async def test_radar_source_takes_only_the_rows_that_meet_the_profile_yield_and_feeds_the_watchlist(tmp_path):
    svc, runner, _ = make(tmp_path)
    assert svc.radar_spreads() is None and await svc.sync_radar_source() is None
    rows = [row("MPC"), row("PSX", strike=275, mid=6.7), row("XYZ", strike=100, mid=0.2)]        # 3,4 %, 2,4 % y 0,2 % del strike
    (tmp_path / "radar.json").write_text(json.dumps(doc(rows)), encoding="utf-8")
    assert svc.radar_min_yield() == 1.0 and svc.radar_passing() == ["MPC", "PSX"]
    res = await svc.sync_radar_source()
    assert svc.manual_sources["RadarSpreads"] == ["MPC", "PSX"] and res["added"] == ["MPC", "PSX"]
    await svc.wait_idle()
    assert set(svc.watchlist.list()) >= {"MPC", "PSX"} and "XYZ" not in svc.watchlist.list()
    assert await svc.sync_radar_source() is None                                                     # mismo JSON y umbral: no se repite


async def test_without_a_runner_nothing_external_is_ever_launched(tmp_path):
    settings = Settings.model_validate({"tortuga": {"radar_path": str(tmp_path / "radar.json")}})
    gw = FakeGateway()
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    assert svc.tortuga is None and svc.tortuga_in_background() is False and svc.tortuga_in_background(force="all") is False


async def test_background_launch_runs_what_is_due_and_then_syncs_the_radar(tmp_path):
    clock_now = datetime(2026, 10, 19, 11, 50)
    svc, runner, clock = make(tmp_path, run_calls=fake_calls(zone_calls({"sell_premium": [row("MPC")]})))
    clock["now"] = clock_now
    assert svc.tortuga_in_background() is True and svc.tortuga_in_background() is False              # no se solapa consigo misma
    await svc.wait_idle()
    state = runner.state()
    assert state["radar_spreads"]["summary"].startswith("1 valores") and state["comunidad"].get("error")        # una falla, otra no
    await svc.wait_idle()
    assert "RadarSpreads" in svc.manual_sources and svc.tortuga_in_background(force="radar_spreads") is True
    await svc.wait_idle()


def test_tortuga_page_renders_rows_routines_and_manual_launch(client_and_service, tmp_path):
    client, svc, gw, _db = client_and_service
    assert "Todavía no hay datos del radar" in client.get("/marketradar").text
    path = tmp_path / "radar.json"
    path.write_text(json.dumps(doc([row("MPC"), row("XYZ", strike=100, mid=0.2)])), encoding="utf-8")
    object.__setattr__(svc.settings, "tortuga", svc.settings.tortuga.model_copy(update={"radar_path": str(path)}))
    html = client.get("/marketradar").text
    assert "MPC" in html and "Put 30Δ" in html and "Prima ($)" in html and 'class="seg"' in html and "✓" in html and "Actualizar todas" not in html
    assert "XYZ" in html and "XYZ" not in client.get("/marketradar?cumple=1").text


def test_the_radar_reads_both_strategies_and_labels_each_row_with_its_kind():
    spreads = [row("MPC", iv_rank=97), row("PSX", iv_rank=80)]
    puts = [put_row("NTAP", iv_rank=64), put_row("XLE", iv_rank=56, sector="ETF")]
    d = build_radar_document(zone_calls({"z": spreads}, puts=puts), NOW)
    data = parse_radar(d)
    assert [r.ticker for r in data.rows] == ["MPC", "PSX", "NTAP", "XLE"]
    assert [r.kind for r in data.rows] == ["spread", "spread", "put", "put"] and data.rows[2].strategy_key == "ventaPut"
    calls = zone_calls({"z": spreads}, puts=puts)
    with pytest.raises(RadarError, match="short_put"):
        build_radar_document(calls[:-1], NOW)                                                     # falta la estrategia de venta de put
    calls[-1] = ToolCall("get_market_radar", {"strategy": "short_put", "limit": 100, "sort": "iv_rank"},
                         json.dumps({"as_of": "2026-10-16", "matched": 9, "rows": puts}))        # recortada: 2 de 9
    with pytest.raises(RadarError, match=r"debería haber 9"):
        build_radar_document(calls, NOW)


def test_the_card_of_a_symbol_adds_the_proposed_spread_to_its_row():
    calls = zone_calls({"z": [row("MPC"), row("PSX")]})
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [180, 170], "premium_per_share": 3.85}
    calls.append(ToolCall("get_market_radar", {"symbol": "MPC"}, json.dumps({"symbol": "MPC", "idea": idea})))
    calls.append(ToolCall("get_market_radar", {"symbol": "PSX"}, json.dumps({"symbol": "PSX", "idea": {**idea, "strategy": "Venta de PUT"}})))
    rows = {r.ticker: r for r in parse_radar(build_radar_document(calls, NOW)).rows}
    mpc = rows["MPC"]
    assert mpc.has_spread and (mpc.spread_short, mpc.spread_long, mpc.spread_dte) == (180, 170, 35) and str(mpc.spread_expiry) == "2026-12-04"
    assert round(mpc.spread_credit_width_pct, 1) == 38.5 and round(mpc.spread_distance_pct, 1) == 62.2
    assert not rows["PSX"].has_spread and rows["PSX"].spread_credit_width_pct is None          # la ficha de otra estrategia no cuenta


async def test_radar_cards_are_fetched_only_for_bull_put_spread_rows_and_a_failure_is_tolerated():
    from scanner_opciones.tortuga.api import TortugaApi
    import httpx
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if q.get("symbol"):
            asked.append(q["symbol"])
            return httpx.Response(404 if q["symbol"] == "BAD" else 200, json={"symbol": q["symbol"], "idea": {"strategy": "Bull Put Spread", "strikes": [10, 5]}})
        rows = [row("OK"), row("BAD"), row("PUT", top_strategy={"key": "ventaPut", "name": "Venta de PUT"})] if q["strategy"] == "bull_put_spread" else []
        return httpx.Response(200, json={"as_of": "2026-10-16", "matched": len(rows), "rows": rows})
    api = TortugaApi("tt_live_x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    calls = await api.radar_calls()
    assert sorted(asked) == ["BAD", "OK"] and [c.input["symbol"] for c in calls if c.input.get("symbol")] == ["OK"]
    d = build_radar_document(calls, NOW)
    assert "idea" in next(r for r in d["rows"] if r["symbol"] == "OK") and all("idea" not in r for r in d["rows"] if r["symbol"] != "OK")


async def test_the_radar_fills_a_source_for_spreads_and_another_for_puts(tmp_path):
    svc, runner, _ = make(tmp_path)
    rows = [row("MPC"), put_row("PSX", strike=275, mid=6.7), put_row("XYZ", strike=100, mid=0.2)]        # el último no llega al yield
    (tmp_path / "radar.json").write_text(json.dumps(doc(rows)), encoding="utf-8")
    assert svc.radar_passing("spread") == ["MPC"] and svc.radar_passing("put") == ["PSX"] and svc.radar_passing() == ["MPC", "PSX"]
    res = await svc.sync_radar_source()
    assert svc.manual_sources["RadarSpreads"] == ["MPC"] and svc.manual_sources["RadarPuts"] == ["PSX"]
    assert res["added"] == ["MPC"] and res["puts"]["added"] == ["PSX"]
    await svc.wait_idle()
    assert not svc.is_renamable_source("RadarPuts")
    assert await svc.sync_radar_source() is None


async def test_old_radar_source_names_are_renamed_on_startup(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.manual_sources = {"TortugaSpreads": ["MPC"], "TortugaPuts": ["PSX"], "Mia": ["AAA"]}
    svc._rename_legacy_radar_sources()
    assert svc.manual_sources == {"RadarSpreads": ["MPC"], "RadarPuts": ["PSX"], "Mia": ["AAA"]}


async def test_old_radar_source_names_are_renamed_in_history_and_saved_profile_filters(tmp_path):
    from scanner_opciones.scanner.profiles import Profile
    svc, _, _ = make(tmp_path)
    svc.meta.set("universe_history", json.dumps([{"name": "TortugaPuts", "at": "2026-10-01T10:00:00"}, {"name": "Mia", "at": "2026-10-01T10:00:00"}]))
    svc.save_profiles([Profile("P", put={"discount": "10", "fs_TortugaSpreads": "on", "fs_Mia": "on"})], "P")
    svc._rename_legacy_radar_sources()
    assert [h["name"] for h in svc._load_history()] == ["RadarPuts", "Mia"]
    assert svc.stored_profiles()[0][0].put == {"discount": "10", "fs_RadarSpreads": "on", "fs_Mia": "on"}


async def test_daily_tortuga_routines_only_run_on_business_days_at_their_time(tmp_path):
    from zoneinfo import ZoneInfo
    mad = ZoneInfo("Europe/Madrid")                                                  # la hora de las rutinas es la local; el calendario, el de Nueva York
    svc, _, clock = make(tmp_path)
    runner = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: clock["now"], fake_claude({"*": "{}"}), tmp_path, fake_calls([]))
    daily = lambda: [r.key for r in runner.due() if r.key not in ("radar_spreads", "radar_puts")]        # noqa: E731
    at = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=mad)                     # noqa: E731
    for key, m in (("aperturas", 10), ("comunidad", 15)):
        runner._save(key, last_ok=at(23, 11, m).isoformat())                         # viernes 11:10 / 11:15
    for d in (24, 25):                                                               # sábado y domingo, a cualquier hora: nada
        for h in (11, 15, 20):
            clock["now"] = at(d, h, 30)
            assert daily() == []
    clock["now"] = at(26, 10, 59)                                                    # lunes antes de su hora: todavía no
    assert daily() == []
    clock["now"] = at(26, 11, 20)                                                    # lunes a las 11:20: toca, como el 1.er día hábil
    assert daily() == ["aperturas", "comunidad"]
    from scanner_opciones.config.settings import MarketSettings
    holiday = svc.settings.model_copy(update={"market": MarketSettings(holidays=[date(2026, 10, 26)])})
    runner = RoutineRunner(holiday, MetaRepo(svc.watchlist.db), lambda: clock["now"], fake_claude({"*": "{}"}), tmp_path, fake_calls([]))
    assert daily() == []                                                             # un lunes festivo se salta igual


async def test_radar_fits_use_the_proposed_spread_for_credit_width(tmp_path):
    from dataclasses import replace
    from scanner_opciones.scanner.criteria import criteria_from_settings
    svc, _, _ = make(tmp_path)
    crit = replace(criteria_from_settings(svc.settings), min_credit_width_pct=30.0, max_credit_width_pct=None)
    svc.profile_criteria_provider = lambda: [("TastySpread", None, crit)]      # lo pone la interfaz web: los filtros grabados en cada perfil
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [440, 430], "premium_per_share": 3.5}
    with_card = parse_radar(doc([row("MPC", idea=idea), row("PSX")])).rows
    fits = svc.radar_fits(with_card, spread=True)
    cw = lambda t: [c for f in fits[t] if f for c in f.criteria if c.label in ("Crédito/ancho", "Yield")]
    assert any(c.label == "Crédito/ancho" and c.ok is not None and "35.0 %" in c.text for c in cw("MPC"))      # 3,5 / 10
    assert all(c.ok is None for c in cw("PSX"))                                                                   # sin ficha: sin dato


def test_a_spread_with_card_passes_by_credit_width_and_without_card_by_put_yield():
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [440, 430], "premium_per_share": 3.5}      # 35 % del ancho
    a, b, c = parse_radar(doc([row("A", idea=idea), row("B", idea={**idea, "premium_per_share": 1.0}), row("C")])).rows               # B: 10 %; C: sin ficha
    assert passes(a, 100.0, 30.0) and not passes(b, 0.0, 30.0)         # con ficha manda el crédito/ancho, no el yield de la put
    assert passes(c, 1.0, 30.0) and not passes(c, 5.0, 30.0)           # sin ficha, el yield de la put de 30Δ (3,41 %)
    assert not passes(a, 100.0) and passes(b, 1.0)                     # sin mínimo de crédito/ancho, como antes


def test_held_details_describe_stocks_spreads_combos_and_loose_options(tmp_path):
    from datetime import date
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    svc, _, _ = make(tmp_path)

    def opt(strike, right, exp=date(2026, 12, 18)):
        return OptionContract(ticker="PLTR", expiry=exp, strike=strike, right=right)
    pos = lambda q, o=None: Position("PLTR", q, 100.0, option=o)
    svc.state.positions = [pos(100), pos(-2, opt(180, OptionRight.PUT)), pos(2, opt(170, OptionRight.PUT)), pos(-1, opt(250, OptionRight.CALL))]
    lines = svc.held_details()["PLTR"]
    assert "Bull Put Spread vendido 180/170, vence 18/12/2026 ×2" in lines and "Call vendida 250, vence 18/12/2026 ×1" in lines
    assert "100 acciones" in lines and len(lines) == 3


def test_held_note_detail_lists_every_position_with_strikes_and_expiries(tmp_path):
    from datetime import date
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    from scanner_opciones.ui.web import held_note
    svc, _, _ = make(tmp_path)
    exp = date(2026, 12, 18)
    svc.state.positions = [Position("PLTR", 100, 1.0), Position("PLTR", -1, 1.0, option=OptionContract(ticker="PLTR", expiry=exp, strike=250, right=OptionRight.CALL))]
    short, detail = held_note(svc.held_tickers(), "PLTR", exp, 180)
    assert short == "ACC C" and "• 100 acciones" in detail and "• Call vendida 250, vence 18/12/2026 ×1" in detail


async def test_a_card_without_premium_is_priced_with_the_live_quotes_of_its_legs(tmp_path):
    from types import SimpleNamespace
    svc, _, _ = make(tmp_path)
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [27, 25], "premium_per_share": None}
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("PFE", idea=idea)])), encoding="utf-8")
    assert svc.radar_spreads().rows[0].spread_credit_width_pct is None

    class Provider:
        async def get_put_listing(self, ticker):
            return {(date(2026, 12, 4), 27.0): "S", (date(2026, 12, 4), 25.0): "L"}

        async def get_option_quotes(self, symbols):
            from scanner_opciones.domain.models import OptionQuote
            return {"S": OptionQuote(bid=0.60, ask=0.70), "L": OptionQuote(bid=0.10, ask=0.20)}

    svc.volatility = Provider()
    await svc.refresh_radar_quotes()
    cw = svc.radar_spreads().rows[0].spread_credit_width_pct
    assert cw is not None and 20 < cw < 30            # vende ~0,6-0,7 y compra a 0,20 sobre un ancho de 2


async def test_radar_premiums_are_net_of_the_opening_commission(tmp_path):
    svc, _, _ = make(tmp_path)
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [180, 170], "premium_per_share": 3.85}
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("MPC", idea=idea)])), encoding="utf-8")
    r = svc.radar_spreads().rows[0]
    assert r.net_of_commissions and abs(r.put_mid - (15.0 - 0.0105)) < 1e-9 and abs(r.spread_premium - (3.85 - 0.011)) < 1e-9
    assert abs(r.distance_pct - (476.65 - (440.0 - r.put_mid)) / 476.65 * 100) < 1e-9                  # descuento hasta el breakeven
    off = Settings.model_validate({"commissions": {"enabled": False}, "tortuga": {"radar_path": str(tmp_path / "radar.json")}})
    svc.settings = off
    assert svc.radar_spreads().rows[0].put_mid == 15.0 and not svc.radar_spreads().rows[0].net_of_commissions


async def test_radar_contracts_are_revalued_with_live_quotes_and_keep_tortugas_data_without_them(tmp_path):
    from types import SimpleNamespace
    svc, _, _ = make(tmp_path)
    idea = {"strategy": "Bull Put Spread", "expiry": "2026-12-04", "dte": 35, "strikes": [440, 430], "premium_per_share": 3.5}
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("MPC", idea=idea), row("PSX")])), encoding="utf-8")
    before = {r.ticker: r for r in svc.radar_spreads().rows}
    assert not before["MPC"].live

    class Provider:
        async def get_put_listing(self, ticker):
            if ticker != "MPC":
                return {}
            return {(date(2026, 12, 4), 440.0): "S", (date(2026, 12, 4), 430.0): "L", (before["MPC"].put_expiry, before["MPC"].put_strike): "P"}

        async def get_option_quotes(self, symbols):
            q = {"S": SimpleNamespace(bid=5.0, ask=5.4, last=None, delta=-0.3, iv=None, open_interest=None, bid_size=None),
                 "L": SimpleNamespace(bid=1.0, ask=1.2, last=None, delta=-0.1, iv=None, open_interest=None, bid_size=None),
                 "P": SimpleNamespace(bid=9.0, ask=9.4, last=None, delta=-0.25, iv=None, open_interest=None, bid_size=None)}
            return {s: q[s] for s in symbols}

        async def get_prices(self, tickers):
            return {t: 500.0 for t in tickers}

    svc.volatility = Provider()
    await svc.refresh_radar_quotes()
    after = {r.ticker: r for r in svc.radar_spreads().rows}
    mpc = after["MPC"]
    assert mpc.live and mpc.price == 500.0 and abs(mpc.put_delta + 0.25) < 1e-9
    assert mpc.put_mid < before["MPC"].put_mid and abs(mpc.spread_premium - (5.1 - 1.2 - 0.011)) < 1e-6      # precio de referencia de la vendida − ask de la comprada, neto de comisión
    assert svc.radar_live_at() is not None
    psx = after["PSX"]
    assert psx.price == 500.0 and not psx.live and psx.put_mid == before["PSX"].put_mid                       # sin cotización: se queda la prima de Tortuga


async def test_radar_live_quotes_survive_a_restart(tmp_path):
    from types import SimpleNamespace
    svc, _, _ = make(tmp_path)
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("MPC")])), encoding="utf-8")
    r0 = svc.radar_spreads().rows[0]

    class Provider:
        async def get_put_listing(self, ticker):
            return {(r0.put_expiry, r0.put_strike): "P"}

        async def get_option_quotes(self, symbols):
            from scanner_opciones.domain.models import OptionQuote
            return {s: OptionQuote(bid=9.0, ask=9.4, delta=-0.25) for s in symbols}
    svc.volatility = Provider()
    await svc.refresh_radar_quotes()
    assert svc.radar_spreads().rows[0].live
    assert svc._load_radar_live() is not None and svc._load_radar_live()[1] == svc._radar_live[1]       # leída de la base de datos
    svc._radar_live = svc._load_radar_live()
    assert svc.radar_spreads().rows[0].live and svc.radar_live_at() is not None


def test_old_tab_addresses_redirect_to_the_new_names(client_and_service):
    client = client_and_service[0]
    r = client.get("/ideas?trader=Ana&tipo=put", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/comunidad?trader=Ana&tipo=put"
    r = client.get("/tortuga?cumple=1", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/marketradar?cumple=1"
    assert client.get("/comunidad").status_code == 200 and client.get("/marketradar").status_code == 200


def put_row(symbol, **over):
    return row(symbol, top_strategy={"key": "ventaPut", "name": "Venta de PUT", "score": 60, "signal": "bullish"}, **over)


async def test_each_radar_routine_writes_its_own_file_and_the_tab_merges_them(tmp_path):
    put_calls = [ToolCall("get_market_radar", {"strategy": "short_put", "sort": "iv_rank", "limit": 100},
                          json.dumps({"as_of": "2026-10-16", "matched": 1, "returned": 1, "rows": [put_row("PSX")]}))]
    svc, runner, _ = make(tmp_path, run_calls=fake_calls(zone_calls({"sell_premium": [row("MPC")]})))
    ok, summary = await runner.run_one("radar_spreads")
    assert ok and "Bull Put Spread" in summary and (tmp_path / "radar.json").exists() and not (tmp_path / "radar_puts.json").exists()
    assert [r.ticker for r in svc.radar_spreads().rows] == ["MPC"]
    puts = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: NOW, fake_claude({}), tmp_path, fake_calls(put_calls))
    ok, summary = await puts.run_one("radar_puts")
    assert ok and "venta de put" in summary and (tmp_path / "radar_puts.json").exists()
    rows = {r.ticker: r.kind for r in svc.radar_spreads().rows}
    assert rows == {"MPC": "spread", "PSX": "put"}
    assert all(puts.state()[k].get("last_ok") for k in ("radar_spreads", "radar_puts"))             # cada rutina lleva su propio estado


async def test_a_radar_file_from_before_the_split_loses_its_puts_once_the_puts_file_exists(tmp_path):
    svc, _, _ = make(tmp_path)
    (tmp_path / "radar.json").write_text(json.dumps(doc([row("MPC"), put_row("OLD")])), encoding="utf-8")
    assert {r.ticker for r in svc.radar_spreads().rows} == {"MPC", "OLD"}                  # sin fichero de puts se usa todo
    (tmp_path / "radar_puts.json").write_text(json.dumps(doc([put_row("PSX")])), encoding="utf-8")
    assert {r.ticker for r in svc.radar_spreads().rows} == {"MPC", "PSX"}


def test_the_puts_radar_follows_the_spreads_schedule_when_config_does_not_name_it():
    from scanner_opciones.tortuga.schedule import default_schedule
    old = Settings.model_validate({"tortuga": {"intervals": {"radar_spreads": 20}, "times": {"radar_spreads": "11:45"}}})
    assert default_schedule(old, "radar_puts").in_market == 20
