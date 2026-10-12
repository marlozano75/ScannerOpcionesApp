"""La comunidad de Tortuga en la aplicación: fuente del Universo, columna del Scanner y aviso de datos viejos."""
import json
import os
from datetime import datetime, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import CommunitySettings, Settings
from scanner_opciones.storage.db import Database
from tests.integration.test_web import BASE, client_and_service, refresh  # noqa: F401

NOW = datetime(2026, 10, 12, 14, 0)


def payload(generated="2026-10-12T13:00:00", tickers=None):
    tickers = tickers if tickers is not None else [
        {"ticker": "PEP", "month_traders": 17, "month_operations": 45, "week_traders": 6},
        {"ticker": "ASTS", "month_traders": 48, "month_operations": 238, "week_traders": 20},
        {"ticker": "RARE", "month_traders": 2, "month_operations": 3},                  # menos de 3 traders: no entra en la fuente
        {"ticker": "MCD", "month_traders": 14, "month_operations": 38},
    ]
    return json.dumps({"version": 1, "generated_at": generated, "tickers": tickers, "ideas": []})


def write(path, text, bump=0):
    path.write_text(text, encoding="utf-8")
    if bump:     # el servicio relee el fichero solo si cambia su fecha de modificación
        stamp = path.stat().st_mtime + bump
        os.utime(path, (stamp, stamp))


def make(tmp_path, **community):
    settings = Settings.model_validate({"community": {"path": str(tmp_path / "tortuga.json"), **community}})
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    for t in ("PEP", "ASTS", "MCD", "RARE"):
        svc._has_options[t] = True
    return svc


async def test_without_a_file_there_is_no_community_and_nothing_breaks(tmp_path):
    svc = make(tmp_path)
    assert svc.community() is None and svc.community_counts() == {} and svc.community_stale() is None
    assert await svc.sync_community_source() is None and "Tortuga" not in svc.manual_sources


async def test_the_top_tickers_become_a_universe_source_only_when_the_file_changes(tmp_path):
    svc = make(tmp_path, universe_top=2)
    write(tmp_path / "tortuga.json", payload())
    res = await svc.sync_community_source()
    assert svc.manual_sources["Tortuga"] == ["ASTS", "PEP"] and res["added"] == ["ASTS", "PEP"]        # por traders; RARE no llega a 3
    assert await svc.sync_community_source() is None                                                    # mismo JSON: no se repite
    write(tmp_path / "tortuga.json", payload("2026-10-13T13:00:00", [{"ticker": "MCD", "month_traders": 14, "month_operations": 38}]), bump=60)
    again = await svc.sync_community_source()
    assert svc.manual_sources["Tortuga"] == ["MCD"] and again["removed"] == ["ASTS", "PEP"]            # la fuente se sustituye entera


async def test_a_broken_file_keeps_the_last_good_data(tmp_path):
    svc = make(tmp_path)
    path = tmp_path / "tortuga.json"
    write(path, payload())
    assert svc.community_counts()["PEP"] == (17, 6)
    write(path, "{ roto", bump=60)
    assert svc.community_counts()["PEP"] == (17, 6)


async def test_stale_data_is_reported_after_the_configured_hours(tmp_path):
    svc = make(tmp_path, stale_hours=24)
    path = tmp_path / "tortuga.json"
    write(path, payload("2026-10-12T13:00:00"))
    assert svc.community_stale() is None
    write(path, payload((NOW - timedelta(days=3)).isoformat()), bump=60)
    assert round(svc.community_stale()) == 72


def test_the_scanner_shows_how_many_traders_sell_puts_on_each_ticker(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert "<th title=\"Traders de la comunidad" in client.get(BASE).text           # la columna está siempre; sin datos sale «—»
    path = tmp_path / "tortuga.json"
    write(path, payload(tickers=[{"ticker": "AAPL", "month_traders": 9, "month_operations": 12, "week_traders": 3}]))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    assert "<td data-sort=\"9\">9 (3)</td>" in client.get(BASE).text


def test_the_scanner_warns_when_the_daily_routine_has_not_run(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    path = tmp_path / "tortuga.json"
    write(path, payload((svc.now() - timedelta(days=5)).isoformat()))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    assert "Los datos de la comunidad de Tortuga tienen 5 días" in client.get(BASE).text
    write(path, payload(svc.now().isoformat()), bump=60)
    assert "Los datos de la comunidad de Tortuga" not in client.get(BASE).text
