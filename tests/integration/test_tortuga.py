"""Tortuga desde la aplicación: radar de Bull Put Spreads, rutinas que lanzan la CLI de Claude (simulada) y fuente TortugaSpreads."""
import json
import os
from datetime import datetime, timedelta

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

NOW = datetime(2026, 10, 17, 12, 0)


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


def zone_calls(rows_by_zone, total=None, **over):
    """Las llamadas a get_market_radar como las devuelve la CLI: la de recuento y una por tramo de precio."""
    every = [r for rows in rows_by_zone.values() for r in rows]
    n = len({r["symbol"] for r in every}) if total is None else total
    out = [ToolCall("get_market_radar", {"strategy": "bull_put_spread", "limit": 1},
                    json.dumps({"as_of": "2026-10-16", "matched": n, "returned": 1, "truncated": True, "rows": every[:1]}))]
    for zone, rows in rows_by_zone.items():
        body = {"as_of": "2026-10-16", "matched": len(rows), "returned": len(rows), "truncated": False, "rows": rows, **over}
        out.append(ToolCall("get_market_radar", {"strategy": "bull_put_spread", "price_min": 0, "price_max": 20, "limit": 100, "tag": zone}, json.dumps(body)))
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
        build_radar_document(zone_calls({"sell_premium": [row("A")]}, matched=59), NOW)
    with pytest.raises(RadarError, match="recortada"):
        build_radar_document(zone_calls({"sell_premium": [row("A")]}, truncated=True), NOW)


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
        "tortuga": {"radar_path": str(tmp_path / "radar.json"), "prompts_dir": str(tmp_path / "prompts"), **tortuga}})
    (tmp_path / "prompts").mkdir(exist_ok=True)
    for name in ("aperturas_prompt.md", "comunidad_prompt.md", "radar_spreads_prompt.md"):
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
                        fake_calls(zone_calls({"sell_premium": [row("A")]}, matched=59)))
    ok, why = await bad.run_one("radar_spreads")
    assert not ok and "recortada" in why and (tmp_path / "radar.json").read_text(encoding="utf-8") == before        # se conserva el bueno
    assert bad.state()["radar_spreads"]["error"] == why


async def test_due_routines_follow_the_schedule_and_back_off_after_a_failure(tmp_path):
    svc, runner, clock = make(tmp_path, fake_claude({}), retry_minutes=60)
    clock["now"] = datetime(2026, 10, 17, 10, 0)
    assert runner.due() == []                                                       # antes de la primera hora (11:10)
    clock["now"] = datetime(2026, 10, 17, 11, 20)
    assert [r.key for r in runner.due()] == ["aperturas", "comunidad"]
    await runner.run_one("aperturas")                                               # falla: no hay respuesta simulada
    assert [r.key for r in runner.due()] == ["comunidad"]                           # no se reintenta enseguida
    clock["now"] = datetime(2026, 10, 17, 12, 30)
    assert "aperturas" in [r.key for r in runner.due()]                             # pasada la hora de espera, sí
    ok_runner = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: clock["now"], fake_claude({}), tmp_path,
                              fake_calls(zone_calls({"sell_premium": [row("MPC")]})))
    await ok_runner.run_one("radar_spreads")
    assert "radar_spreads" not in [r.key for r in ok_runner.due()]                  # ya salió bien hoy
    clock["now"] = datetime(2026, 10, 18, 11, 50)
    assert "radar_spreads" in [r.key for r in ok_runner.due()]                      # y mañana vuelve a tocar


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
    assert svc.manual_sources["TortugaSpreads"] == ["MPC", "PSX"] and res["added"] == ["MPC", "PSX"]
    await svc.wait_idle()
    assert set(svc.watchlist.list()) >= {"MPC", "PSX"} and "XYZ" not in svc.watchlist.list()
    assert await svc.sync_radar_source() is None                                                     # mismo JSON y umbral: no se repite


async def test_without_a_runner_nothing_external_is_ever_launched(tmp_path):
    settings = Settings.model_validate({"tortuga": {"radar_path": str(tmp_path / "radar.json")}})
    gw = FakeGateway()
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    assert svc.tortuga is None and svc.tortuga_in_background() is False and svc.tortuga_in_background(force="all") is False


async def test_background_launch_runs_what_is_due_and_then_syncs_the_radar(tmp_path):
    clock_now = datetime(2026, 10, 17, 11, 50)
    svc, runner, clock = make(tmp_path, run_calls=fake_calls(zone_calls({"sell_premium": [row("MPC")]})))
    clock["now"] = clock_now
    assert svc.tortuga_in_background() is True and svc.tortuga_in_background() is False              # no se solapa consigo misma
    await svc.wait_idle()
    state = runner.state()
    assert state["radar_spreads"]["summary"].startswith("1 valores") and state["comunidad"].get("error")        # una falla, otra no
    await svc.wait_idle()
    assert "TortugaSpreads" in svc.manual_sources and svc.tortuga_in_background(force="radar_spreads") is True
    await svc.wait_idle()


def test_tortuga_page_renders_rows_routines_and_manual_launch(client_and_service, tmp_path):
    client, svc, gw, _db = client_and_service
    assert "Todavía no hay datos del radar" in client.get("/tortuga").text
    path = tmp_path / "radar.json"
    path.write_text(json.dumps(doc([row("MPC"), row("XYZ", strike=100, mid=0.2)])), encoding="utf-8")
    object.__setattr__(svc.settings, "tortuga", svc.settings.tortuga.model_copy(update={"radar_path": str(path)}))
    html = client.get("/tortuga").text
    assert "MPC" in html and "Strike 30Δ" in html and "Yield periodo" in html and "✓" in html and "Actualizar todas" in html
    assert "XYZ" in html and "XYZ" not in client.get("/tortuga?cumple=1").text
    r = client.post("/tortuga/run", data={"key": "nope"})
    assert r.status_code == 400
    assert client.post("/tortuga/run", data={"key": "all"}, follow_redirects=False).status_code == 303
