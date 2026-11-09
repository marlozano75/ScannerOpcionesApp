"""Las cachés de velas no se vacían sin motivo: `cleanup_orphans` se ejecuta antes de cada refresco y, si lo vaciaba aunque no borrara nada,
la primera página tras cada refresco tardaba ~20 s en volver a limpiar las velas de todos los tickers (2026-11-09)."""
from datetime import date, timedelta

from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import BarRepo

TODAY = date(2026, 11, 9)


def _repo() -> BarRepo:
    repo = BarRepo(Database(":memory:"))
    for t in ("AAA", "BBB"):
        repo.upsert_ohlc(t, [(TODAY - timedelta(days=i), 10.0 + i, 11.0 + i, 9.0 + i, 10.0 + i) for i in range(5)])
    return repo


def test_purge_except_sin_borrar_conserva_las_cachés():
    repo = _repo()
    repo.all_closes({"AAA", "BBB"})
    recent = repo.recent_candles(TODAY - timedelta(days=30))
    version = repo.version
    assert repo.purge_except({"AAA", "BBB"}) == 0
    assert repo.version == version
    assert repo._cache and repo.recent_candles(TODAY - timedelta(days=30)) is recent      # mismo objeto: sin releer


def test_purge_except_que_borra_invalida():
    repo = _repo()
    repo.all_closes({"AAA", "BBB"})
    recent = repo.recent_candles(TODAY - timedelta(days=30))
    assert repo.purge_except({"AAA"}) > 0
    assert not repo._cache
    after = repo.recent_candles(TODAY - timedelta(days=30))
    assert after is not recent and set(after) == {"AAA"}


def test_nuevas_velas_invalidan_las_recientes():
    repo = _repo()
    first = repo.recent_candles(TODAY - timedelta(days=30))
    repo.upsert_ohlc("AAA", [(TODAY + timedelta(days=1), 20.0, 21.0, 19.0, 20.0)])
    assert repo.recent_candles(TODAY - timedelta(days=30)) is not first
