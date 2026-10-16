"""Universo para bull put spreads en la aplicación: fuente del Universo, tickers «solo spread», ETF exentos y aviso de datos viejos."""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database
from scanner_opciones.watchlist.parser import parse_tokens
from tests.integration.test_web import BASE, client_and_service, refresh  # noqa: F401

NOW = datetime(2026, 10, 17, 12, 0)


def payload(generated="2026-10-17T10:00:00", members=None):
    members = members or [
        {"ticker": "SPY", "kind": "ETF", "sector": "Índice EE.UU.", "level": "A", "radar": True},
        {"ticker": "AAPL", "kind": "Acción", "sector": "Tecnología", "level": "B"},
        {"ticker": "HOOD", "kind": "Acción", "sector": "Financiero", "level": "D"},       # nivel D: desactivado
    ]
    return json.dumps({"version": 1, "generated_at": generated, "members": members})


def make(tmp_path, **cfg):
    settings = Settings.model_validate({"spread_universe": {"path": str(tmp_path / "bps.json"), **cfg}})
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    for t in ("SPY", "AAPL", "HOOD", "MSFT", "NEW"):
        svc._has_options[t] = True
    return svc


def write(path, text, bump=0):
    path.write_text(text, encoding="utf-8")
    if bump:
        stamp = path.stat().st_mtime + bump
        os.utime(path, (stamp, stamp))


async def test_without_a_file_nothing_happens(tmp_path):
    svc = make(tmp_path)
    assert svc.spread_universe() is None and svc.spread_universe_stale() is None
    assert await svc.sync_spread_universe_source() is None and svc.spread_only_tickers() == frozenset()


async def test_active_members_become_the_source_and_only_when_the_file_changes(tmp_path):
    svc = make(tmp_path)
    write(tmp_path / "bps.json", payload())
    res = await svc.sync_spread_universe_source()
    assert svc.manual_sources["Universo_BPS"] == ["SPY", "AAPL"] and res["added"] == ["SPY", "AAPL"]   # HOOD (nivel D) no entra
    assert await svc.sync_spread_universe_source() is None
    write(tmp_path / "bps.json", payload("2026-10-24T10:00:00", [{"ticker": "SPY", "kind": "ETF", "sector": "x", "level": "A"}]), bump=60)
    again = await svc.sync_spread_universe_source()
    assert svc.manual_sources["Universo_BPS"] == ["SPY"] and again["removed"] == ["AAPL"]


async def test_spread_only_means_all_its_sources_are_spread_sources(tmp_path):
    svc = make(tmp_path)
    write(tmp_path / "bps.json", payload())
    await svc.sync_spread_universe_source()
    await svc.add_manual_tickers(parse_tokens(["AAPL", "MSFT"]), "MisCalidad")        # AAPL también está en una fuente normal
    assert svc.spread_only_tickers() == {"SPY"}
    svc.set_spread_source("MisCalidad", True)                                          # marcarla como apta para spreads
    assert svc.spread_only_tickers() == {"SPY", "AAPL", "MSFT"}
    svc.set_spread_source("MisCalidad", False)
    assert svc.spread_only_tickers() == {"SPY"}


async def test_the_builtin_source_cannot_be_unmarked_and_unknown_sources_fail(tmp_path):
    import pytest
    from scanner_opciones.domain.errors import WatchlistError
    svc = make(tmp_path)
    write(tmp_path / "bps.json", payload())
    await svc.sync_spread_universe_source()
    svc.set_spread_source("Universo_BPS", False)
    assert "Universo_BPS" in svc.spread_source_names()
    with pytest.raises(WatchlistError):
        svc.set_spread_source("NoExiste", True)


async def test_etfs_of_the_universe_are_exempt_from_company_filters(tmp_path):
    svc = make(tmp_path)
    assert "=NEWETF" not in svc.quality_exempt()
    write(tmp_path / "bps.json", payload(members=[{"ticker": "newetf", "kind": "ETF", "sector": "x", "level": "A"}]))
    assert "=NEWETF" in svc.quality_exempt() and "=SPY" in svc.quality_exempt()           # los de config.etf_index_symbols siguen


async def test_stale_file_is_reported_and_a_broken_one_keeps_the_last_good(tmp_path):
    svc = make(tmp_path, stale_days=5)
    path = tmp_path / "bps.json"
    write(path, payload("2026-10-01T10:00:00"))
    assert svc.spread_universe_stale() == 16
    write(path, "{ roto", bump=60)
    assert svc.spread_universe() is not None and svc.spread_universe_stale() == 16
    write(path, payload("2026-10-16T10:00:00"), bump=120)
    assert svc.spread_universe_stale() is None


def test_cli_builds_validates_and_keeps_the_previous_file_on_bad_input(tmp_path):
    root = Path(__file__).resolve().parents[2]
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"spread_universe:\n  path: {(tmp_path / 'u.json').as_posix()}\n  seed_path: {(root / 'universo' / 'seed_bps.json').as_posix()}\n"
                   f"  history_path: {(tmp_path / 'h.json').as_posix()}\n", encoding="utf-8")
    rows = [{"ticker": f"T{i}", "sector": "Technology", "liquidity": 4, "price": 100} for i in range(25)] + \
           [{"ticker": "AAPL", "sector": "Technology", "liquidity": 4, "price": 300}]
    good, short = tmp_path / "radar.json", tmp_path / "short.json"
    good.write_text(json.dumps({"version": 1, "rows": rows}), encoding="utf-8")
    short.write_text(json.dumps({"version": 1, "rows": rows[:3]}), encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, "-I", "-m", "scanner_opciones.universe_bps", *a], capture_output=True, text=True,
                                    encoding="utf-8", cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")})
    assert run("construir", str(short), str(tmp_path / "u.json"), str(cfg)).returncode == 1 and not (tmp_path / "u.json").exists()
    ok = run("construir", str(good), str(tmp_path / "u.json"), str(cfg))
    assert ok.returncode == 0, ok.stderr
    assert run("validar", str(tmp_path / "u.json")).returncode == 0
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    assert run("validar", str(tmp_path / "bad.json")).returncode == 1


def test_universe_page_offers_the_spread_toggle(client_and_service):
    client, svc, *_ = client_and_service
    svc.manual_sources["Manual1"] = ["AAPL"]
    svc.manual_sources["Universo_BPS"] = ["AAPL"]
    page = client.get("/universe?src=Manual1").text
    assert "Marcar como fuente apta para spreads" in page and "/universe/source/spread" in page
    r = client.post("/universe/source/spread", data={"src": "Manual1", "on": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "Manual1" in svc.spread_source_names()
    assert "Fuente apta para spreads" in client.get("/universe?src=Manual1").text
    assert "no se puede desmarcar" in client.get("/universe?src=Universo_BPS").text
