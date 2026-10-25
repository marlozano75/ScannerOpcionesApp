"""Super Investors de Tortuga (13F de gestores de calidad) como fuente del Universo para puts."""
import json
from datetime import datetime

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import MetaRepo
from scanner_opciones.tortuga.claude_runner import ToolCall
from scanner_opciones.tortuga.investors import InvestorsError, build_investors_document, parse_investors, quality_holdings
from scanner_opciones.tortuga.routines import RoutineRunner

NOW = datetime(2026, 10, 20, 12, 0)     # martes
MANAGERS = ["buffett", "fundsmith"]


def portfolio(slug, name, positions):
    return ToolCall("get_super_investors", {"investor": slug, "limit": 50}, json.dumps({
        "slug": slug, "name": name, "fund": "F", "latest_quarter": "2026-06-30", "positions": [
            {"ticker": t, "issuer": t + " INC", "status": st, "weight_pct": w} for t, w, st in positions]}))


CALLS = [
    portfolio("buffett", "Warren Buffett", [("AAPL", 20.0, "trimmed"), ("BRK/B", 3.0, "held"), ("KO", 1.0, "held")]),
    portfolio("fundsmith", "Terry Smith", [("MSFT", 6.0, "held"), ("KO", 2.5, "added"), ("OLD", 4.0, "exited")]),
]


def test_document_is_built_from_the_raw_results_and_all_managers_are_required():
    doc = build_investors_document(CALLS, NOW, MANAGERS)
    data = parse_investors(doc)
    assert [m.slug for m in data.managers] == ["buffett", "fundsmith"]
    assert "BRK-B" in {h.ticker for h in data.managers[0].holdings}                 # la barra de Tortuga se traduce al formato de la app
    try:
        build_investors_document(CALLS[:1], NOW, MANAGERS)
    except InvestorsError as exc:
        assert "fundsmith" in str(exc)
    else:
        raise AssertionError("faltaba un gestor y se aceptó")


def test_quality_holdings_keep_conviction_positions_and_drop_exited_ones():
    data = parse_investors(build_investors_document(CALLS, NOW, MANAGERS))
    held = quality_holdings(data, 2.0)
    assert set(held) == {"AAPL", "BRK-B", "MSFT", "KO"}                              # KO: 1 % en uno y 2,5 % en otro -> cuenta por el segundo
    assert held["KO"]["managers"] == ["Terry Smith"] and "OLD" not in held           # OLD ya vendida del todo
    assert list(quality_holdings(data, 0.5, min_managers=2)) == ["KO"]


def make(tmp_path, calls):
    settings = Settings.model_validate({
        "super_investors": {"path": str(tmp_path / "si.json"), "managers": MANAGERS},
        "tortuga": {"prompts_dir": str(tmp_path / "prompts")}})
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "super_investors_prompt.md").write_text("investors:\n{managers}", encoding="utf-8")
    db = Database(tmp_path / "app.db")
    seen = []

    async def run_calls(command, prompt, tools, model, max_turns, timeout):
        seen.append(prompt)
        return list(calls)

    runner = RoutineRunner(settings, MetaRepo(db), lambda: NOW, None, tmp_path, run_calls)
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, db, settings, lambda: NOW, tortuga=runner)
    for t in ("AAPL", "BRK-B", "MSFT", "KO"):
        svc._has_options[t] = True
    return svc, runner, seen


async def test_routine_writes_the_file_and_fills_a_put_source(tmp_path):
    svc, runner, seen = make(tmp_path, CALLS)
    ok, summary = await runner.run_one("super_investors")
    assert ok and "2 gestores" in summary and (tmp_path / "si.json").exists()
    assert "- buffett" in seen[0] and "- fundsmith" in seen[0]                       # los gestores salen de la configuración
    result = await svc.sync_investors_source()
    assert set(result["added"]) == {"AAPL", "BRK-B", "MSFT", "KO"}
    assert svc.manual_sources["SuperInvestors"]
    assert not svc.is_renamable_source("SuperInvestors") and not svc.watchlist.list()
    assert await svc.sync_investors_source() is None                                  # sin cambios, no se vuelve a volcar
    assert any(s.name == "SuperInvestors" for s in svc.universe_sources)
    assert svc.investors_info()["KO"]["managers"] == ["Terry Smith"]


async def test_an_incomplete_answer_keeps_the_previous_file(tmp_path):
    svc, runner, _ = make(tmp_path, CALLS)
    assert (await runner.run_one("super_investors"))[0]
    before = (tmp_path / "si.json").read_text(encoding="utf-8")
    bad = RoutineRunner(svc.settings, MetaRepo(svc.watchlist.db), lambda: NOW, None, tmp_path, lambda *a: _calls(CALLS[:1]))
    ok, why = await bad.run_one("super_investors")
    assert not ok and "fundsmith" in why and (tmp_path / "si.json").read_text(encoding="utf-8") == before


async def _calls(calls):
    return list(calls)


async def test_the_routine_is_weekly_not_daily(tmp_path):
    svc, runner, _ = make(tmp_path, CALLS)
    assert "super_investors" in {r.key for r in runner.due()}
    assert (await runner.run_one("super_investors"))[0]
    from datetime import timedelta
    runner.now = lambda: NOW + timedelta(days=1, hours=1)
    assert "super_investors" not in {r.key for r in runner.due()}
    runner.now = lambda: NOW + timedelta(days=8)
    assert "super_investors" in {r.key for r in runner.due()}
