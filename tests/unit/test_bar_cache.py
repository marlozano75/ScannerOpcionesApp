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


# ---- carga por lotes y en una sola pasada (2026-11-12) ------------------------------------------------------------------------------------

def _mixed_repo() -> BarRepo:
    """Velas completas, un ticker solo con cierres (sin máximo ni mínimo) y un cierre provisional del día en curso."""
    repo = _repo()
    repo.upsert("CCC", [(TODAY - timedelta(days=i), 50.0 + i) for i in range(4)])
    repo.upsert_provisional("AAA", TODAY + timedelta(days=1), 12.5)
    return repo


def _one_by_one(repo: BarRepo, tickers) -> dict:
    fresh = BarRepo(repo.db)
    return {t: bars for t in tickers if (bars := fresh._bars(t))}


def test_la_carga_por_lotes_da_los_mismos_cierres_que_ticker_a_ticker():
    repo = _mixed_repo()
    repo._drop_cache()
    tickers = {"AAA", "BBB", "CCC", "ZZZ"}
    assert repo.all_closes(tickers) == _one_by_one(repo, tickers)
    assert repo._cache["ZZZ"] == []      # sin velas guardadas: no se vuelve a consultar


def test_la_pasada_única_da_los_mismos_cierres_y_velas_que_las_consultas(monkeypatch):
    repo = _mixed_repo()
    since = TODAY - timedelta(days=2)
    direct_all, direct_since = repo.recent_candles(date.min), repo.recent_candles(since)
    repo._drop_cache()
    repo._recent.clear()
    monkeypatch.setattr(BarRepo, "LOAD_ALL_FROM", 2)
    tickers = {"AAA", "BBB", "CCC"}
    assert repo.all_closes(tickers) == _one_by_one(repo, tickers)
    assert repo._recent[date.min][1] == direct_all and "CCC" not in direct_all      # las velas oficiales salen de la misma pasada
    assert repo.recent_candles(since) == direct_since                               # y un periodo más corto se recorta de ellas
    assert all(d <= TODAY for bars in direct_all.values() for d, *_ in bars)        # el cierre provisional no cuenta como vela


def test_la_pasada_única_no_pisa_un_ticker_ya_cargado(monkeypatch):
    repo = _mixed_repo()
    monkeypatch.setattr(BarRepo, "LOAD_ALL_FROM", 2)
    loaded = repo._bars("AAA")
    repo.all_closes({"AAA", "BBB", "CCC"})
    assert repo._cache["AAA"] is loaded


def test_un_periodo_más_corto_se_recorta_del_más_largo_ya_leído():
    repo = _repo()
    short = repo.recent_candles(TODAY - timedelta(days=2))
    repo._recent.clear()
    repo.recent_candles(TODAY - timedelta(days=30))
    assert repo.recent_candles(TODAY - timedelta(days=2)) == short
    assert repo.recent_candles(TODAY + timedelta(days=5)) == {}      # sin velas en el periodo: el ticker no aparece
