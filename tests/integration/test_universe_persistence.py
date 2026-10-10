"""Los ficheros del Universo se guardan junto a la base de datos REALMENTE en uso, nunca en `data/` por defecto.

Un script de diagnóstico con la configuración por defecto y una base en memoria llegó a pisar el HelloStocks real del
usuario (2026-10-08): la carpeta se calculaba desde `Settings().storage.path`, que apunta a `data/app.db`."""
from datetime import datetime

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.storage.db import Database
from scanner_opciones.universe.sources import load_sources
from tests.integration.test_web import HELLO_NAME, hello_xlsx

NOW = datetime(2026, 10, 8, 12, 0)


def sources_of(tmp_path):
    path = tmp_path / "upload.xlsx"
    content = hello_xlsx()
    path.write_bytes(content)
    return load_sources(path, HELLO_NAME), content


def test_a_service_with_an_in_memory_database_never_writes_universe_files(tmp_path):
    svc = AppService(FakeGateway(), Database(":memory:"), Settings(), lambda: NOW)      # configuración por defecto: data/app.db
    sources, content = sources_of(tmp_path)
    assert svc._universe_dir() is None
    svc.set_universe_file(HELLO_NAME, sources, content)
    svc.remove_source(sources[0].name)
    assert not (tmp_path / "data").exists() and not any(p.name == "universe" for p in tmp_path.rglob("*"))


def test_the_folder_comes_from_the_database_in_use_not_from_the_settings(tmp_path):
    other = tmp_path / "otra_ubicacion"
    db = Database(other / "app.db")                                                      # la configuración dice data/app.db
    svc = AppService(FakeGateway(), db, Settings(), lambda: NOW)
    assert svc._universe_dir() == other / "universe"
    sources, content = sources_of(tmp_path)
    svc.set_universe_file(HELLO_NAME, sources, content)
    assert (other / "universe" / HELLO_NAME).read_bytes() == content
    assert not (tmp_path / "data").exists()                                              # nada en la ruta por defecto


def test_files_survive_a_restart_with_the_same_database(tmp_path):
    db = Database(tmp_path / "real" / "app.db")
    first = AppService(FakeGateway(), db, Settings(), lambda: NOW)
    sources, content = sources_of(tmp_path)
    first.set_universe_file(HELLO_NAME, sources, content)
    again = AppService(FakeGateway(), db, Settings(), lambda: NOW)                      # reinicio
    assert HELLO_NAME in again.universe_files
    assert {s.name for s in again.universe_sources} >= {src.name for src in sources}


def test_database_remembers_where_it_lives(tmp_path):
    assert Database(":memory:").path == ":memory:"
    assert Database(tmp_path / "x" / "app.db").path == str(tmp_path / "x" / "app.db")
