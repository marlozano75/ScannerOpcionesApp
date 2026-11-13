"""Copia en disco de las velas limpias para el arranque en frío (`storage/bar_snapshot.py`, `BarRepo._load_all`)."""
import random
from datetime import date, timedelta

import pytest

from scanner_opciones.marketdata.bar_cleaning import appends_cleanly, clean_bars
from scanner_opciones.storage import bar_snapshot
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import BarRepo

DAY0 = date(2026, 1, 5)


def _fill(repo: BarRepo, tickers=("AAA", "BBB", "CCC"), days=30):
    for n, t in enumerate(tickers):
        repo.upsert_ohlc(t, [(DAY0 + timedelta(days=i), 10.0 + n + i * 0.1, 10.5 + n + i * 0.1, 9.5 + n + i * 0.1, 10.0 + n, 1000.0)
                             for i in range(days)])
    repo.upsert_ohlc("SPL", [(DAY0 + timedelta(days=i), 100.0 if i < 10 else 25.0, 101.0 if i < 10 else 26.0, 99.0 if i < 10 else 24.0)
                             for i in range(days)])      # split 4:1 sin ajustar


def _load(path, monkeypatch=None, forbid_db=False):
    """Repositorio nuevo (un arranque) con la carga en frío de todo; devuelve (repo, cierres, velas)."""
    repo = BarRepo(Database(path))
    if forbid_db:
        monkeypatch.setattr(repo, "_read_all", lambda: pytest.fail("debía salir de la copia en disco"))
    repo._load_all()
    if repo.snapshot_saver is not None:
        repo.snapshot_saver.join()
    return repo, {t: list(v) for t, v in repo._cache.items()}, repo.recent_candles(date.min)


def test_ida_y_vuelta_da_lo_mismo_que_leer_la_base(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    _, closes, candles = _load(path)
    assert (tmp_path / "app.bars").is_file()
    _, closes2, candles2 = _load(path, monkeypatch, forbid_db=True)
    assert closes2 == closes and candles2 == candles
    assert abs(closes["SPL"][0][1] - 25.0) < 0.01           # la copia guarda los cierres YA limpios (split reescalado)


def test_una_escritura_oficial_invalida_la_copia(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    _load(path)
    BarRepo(Database(path)).upsert_ohlc("AAA", [(DAY0 + timedelta(days=60), 20.0, 21.0, 19.0)])
    read = []
    repo = BarRepo(Database(path))
    original = repo._read_all
    monkeypatch.setattr(repo, "_read_all", lambda: read.append(1) or original())
    repo._load_all()
    repo.snapshot_saver.join()
    assert read and repo._cache["AAA"][-1] == (DAY0 + timedelta(days=60), 20.0)
    _, closes, _ = _load(path, monkeypatch, forbid_db=True)          # y el arranque siguiente ya usa la copia nueva
    assert closes["AAA"][-1] == (DAY0 + timedelta(days=60), 20.0)


@pytest.mark.parametrize("write", ["delete", "prune", "purge"])
def test_borrar_velas_también_invalida_la_copia(tmp_path, write):
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    _load(path)
    repo = BarRepo(Database(path))
    before = bar_snapshot.fingerprint(repo.db.conn)
    {"delete": lambda: repo.delete("AAA"), "prune": lambda: repo.prune(DAY0 + timedelta(days=3)),
     "purge": lambda: repo.purge_except(["AAA", "BBB", "SPL"])}[write]()
    assert bar_snapshot.fingerprint(repo.db.conn) != before


def test_los_provisionales_no_invalidan_la_copia_y_se_superponen(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    _load(path)
    repo = BarRepo(Database(path))
    tag = bar_snapshot.fingerprint(repo.db.conn)
    today = DAY0 + timedelta(days=30)
    repo.upsert_provisional("AAA", today, 13.5)       # salto normal: se añade tal cual
    repo.upsert_provisional("BBB", today, 50.0)       # salto de razón ~4: obliga a volver a limpiar el ticker de la base
    assert bar_snapshot.fingerprint(repo.db.conn) == tag
    fresh, closes, _ = _load(path, monkeypatch, forbid_db=True)
    for t in ("AAA", "BBB", "CCC", "SPL"):
        expected = clean_bars([(date.fromisoformat(r[0]), r[1], r[2], r[3]) for r in fresh.db.conn.execute(
            "SELECT day, close, high, low FROM daily_bars WHERE ticker = ? ORDER BY day", (t,))])
        assert closes[t] == expected, t
    assert closes["AAA"][-1] == (today, 13.5)
    assert fresh._prov() == {("AAA", today), ("BBB", today)}


def test_sin_copia_en_memoria_y_copia_ilegible(tmp_path, monkeypatch):
    assert bar_snapshot.path_for(":memory:") is None
    repo = BarRepo(Database(":memory:"))
    _fill(repo)
    repo._load_all()
    assert repo.snapshot_saver is None
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    (tmp_path / "app.bars").write_bytes(b"basura")
    _, closes, _ = _load(path)                       # se ignora y se reescribe
    _, closes2, _ = _load(path, monkeypatch, forbid_db=True)
    assert closes2 == closes


def test_añadir_un_cierre_que_pasa_la_regla_no_cambia_la_limpieza_anterior():
    """La regla de `appends_cleanly` frente a la limpieza completa, con series al azar que incluyen picos y splits."""
    rng = random.Random(3)
    checked = 0
    for _ in range(3000):
        price, rows = rng.uniform(5, 200), []
        for i in range(rng.randint(2, 25)):
            price *= rng.choice([1.0] * 8 + [2.0, 0.5, 4.0, 0.25, 1.9, 0.53, 3.0])
            price *= rng.uniform(0.95, 1.05)
            hl = rng.random() < 0.7
            rows.append((DAY0 + timedelta(days=i), price, price * 1.02 if hl else None, price * 0.98 if hl else None))
        base = clean_bars(rows)
        if not base:
            continue
        new = (DAY0 + timedelta(days=len(rows)), base[-1][1] * rng.choice([rng.uniform(0.5, 1.8), 0.57, 1.75]), None, None)
        if appends_cleanly(base[-1][1], new[1]):
            checked += 1
            assert clean_bars(rows + [new]) == base + [new[:2]]
    assert checked > 1000


def test_refresh_snapshot_deja_la_copia_al_día_tras_escrituras_sin_carga_completa(tmp_path, monkeypatch):
    """La actualización diaria escribe todos los tickers; nada asegura que después haya una carga en frío que reescriba la copia."""
    path = tmp_path / "app.db"
    _fill(BarRepo(Database(path)))
    repo, _, _ = _load(path)
    repo.upsert_ohlc("AAA", [(DAY0 + timedelta(days=61), 21.0, 22.0, 20.0)])
    assert bar_snapshot.stored_tag(repo._snapshot) != bar_snapshot.fingerprint(repo.db.conn)
    assert repo.refresh_snapshot() is True
    assert repo.refresh_snapshot() is False                         # ya vale: no se reescribe
    _, closes, _ = _load(path, monkeypatch, forbid_db=True)
    assert closes["AAA"][-1] == (DAY0 + timedelta(days=61), 21.0)
    assert not list(tmp_path.glob("*.tmp"))
    assert BarRepo(Database(":memory:")).refresh_snapshot() is False
