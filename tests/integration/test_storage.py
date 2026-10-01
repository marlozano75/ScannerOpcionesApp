from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import (
    ContractRepo, IVHistoryRepo, SnapshotRepo, TickerInfoRepo, WatchlistRepo,
)

NOW = datetime(2026, 9, 29, 10, 0)
TODAY = NOW.date()


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_migration_sets_version_and_is_idempotent(db):
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    db.migrate()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_file_database_persists(tmp_path):
    p = tmp_path / "sub" / "app.db"
    d = Database(p)
    WatchlistRepo(d).add(["AAPL"], NOW)
    d.close()
    assert WatchlistRepo(Database(p)).list() == ["AAPL"]


class TestWatchlistRepo:
    def test_add_returns_only_new(self, db):
        r = WatchlistRepo(db)
        assert r.add(["AAPL", "MSFT"], NOW) == ["AAPL", "MSFT"]
        assert r.add(["MSFT", "KO"], NOW) == ["KO"]
        assert r.list() == ["AAPL", "KO", "MSFT"]

    def test_remove(self, db):
        r = WatchlistRepo(db)
        r.add(["AAPL", "KO"], NOW)
        r.remove("AAPL")
        assert r.list() == ["KO"]

    def test_pending_daily_update(self, db):
        r = WatchlistRepo(db)
        r.add(["AAPL", "KO", "PEP"], NOW)
        r.mark_daily_updated("AAPL", NOW)
        r.mark_daily_updated("KO", NOW - timedelta(days=1))
        # AAPL actualizado hoy; KO ayer; PEP nunca
        assert r.pending_daily_update(TODAY) == ["KO", "PEP"]

    def test_ticker_added_after_daily_run_is_pending(self, db):
        r = WatchlistRepo(db)
        r.add(["AAPL"], NOW)
        r.mark_daily_updated("AAPL", NOW)
        assert r.pending_daily_update(TODAY) == []
        r.add(["NVDA"], NOW + timedelta(hours=2))
        assert r.pending_daily_update(TODAY) == ["NVDA"]


def test_ticker_info_upsert_and_get(db):
    r = TickerInfoRepo(db)
    assert r.get("AAPL") is None
    r.upsert(TickerInfo("AAPL", sector="Technology", underlying_price=200.0, iv_rank=40.0, updated_daily_at=NOW))
    r.upsert(TickerInfo("AAPL", sector="Technology", underlying_price=210.0, days_to_ex_dividend=5))
    got = r.get("AAPL")
    assert got.underlying_price == 210.0 and got.days_to_ex_dividend == 5 and got.iv_rank is None
    assert set(r.all()) == {"AAPL"}


class TestIVHistoryRepo:
    def test_incremental(self, db):
        r = IVHistoryRepo(db)
        assert r.last_day("AAPL") is None
        r.add("AAPL", [(date(2026, 9, 1), 0.2), (date(2026, 9, 2), 0.25)])
        assert r.last_day("AAPL") == date(2026, 9, 2)
        r.add("AAPL", [(date(2026, 9, 2), 0.26), (date(2026, 9, 3), 0.3)])  # reemplaza el 2
        assert r.series("AAPL") == [
            (date(2026, 9, 1), 0.2), (date(2026, 9, 2), 0.26), (date(2026, 9, 3), 0.3)
        ]

    def test_series_since_and_isolation(self, db):
        r = IVHistoryRepo(db)
        r.add("AAPL", [(date(2026, 9, 1), 0.2), (date(2026, 9, 5), 0.3)])
        r.add("KO", [(date(2026, 9, 5), 0.1)])
        assert r.series("AAPL", since=date(2026, 9, 2)) == [(date(2026, 9, 5), 0.3)]

    def test_prune(self, db):
        r = IVHistoryRepo(db)
        r.add("AAPL", [(date(2025, 1, 1), 0.2), (date(2026, 9, 5), 0.3)])
        r.prune("AAPL", date(2025, 9, 29))
        assert len(r.series("AAPL")) == 1


class TestContractsAndSnapshots:
    def _c(self, strike, expiry=date(2026, 10, 30)):
        return OptionContract("AAPL", expiry, strike)

    def test_replace_for_ticker(self, db):
        r = ContractRepo(db)
        r.replace_for_ticker("AAPL", [self._c(150), self._c(155), self._c(155)])  # duplicado ignorado
        assert [c.strike for c in r.list("AAPL")] == [150, 155]
        r.replace_for_ticker("AAPL", [self._c(140)])
        assert [c.strike for c in r.list()] == [140]

    def test_snapshot_roundtrip_and_update(self, db):
        contracts = ContractRepo(db)
        snaps = SnapshotRepo(db)
        c = self._c(150)
        contracts.replace_for_ticker("AAPL", [c])
        assert snaps.upsert(ContractSnapshot(c, NOW, bid=1.0, ask=1.2, open_interest=500, spread_pct=18.2))
        assert snaps.upsert(ContractSnapshot(c, NOW + timedelta(minutes=5), bid=1.1, ask=1.3))
        got = snaps.all("AAPL")
        assert len(got) == 1 and got[0].bid == 1.1 and got[0].updated_at == NOW + timedelta(minutes=5)
        assert got[0].contract == c

    def test_snapshot_for_unknown_contract(self, db):
        assert SnapshotRepo(db).upsert(ContractSnapshot(self._c(150), NOW)) is False

    def test_replacing_contracts_drops_snapshots(self, db):
        contracts, snaps = ContractRepo(db), SnapshotRepo(db)
        c = self._c(150)
        contracts.replace_for_ticker("AAPL", [c])
        snaps.upsert(ContractSnapshot(c, NOW, bid=1.0, ask=1.2))
        contracts.replace_for_ticker("AAPL", [self._c(145)])
        assert snaps.all() == []


def test_migration_from_v1_keeps_existing_snapshots(tmp_path):
    """Una base de datos creada con la versión 1 (sin bid_size) se migra sin perder datos."""
    import sqlite3

    from scanner_opciones.storage.db import MIGRATIONS

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO contracts (ticker, expiry, strike, right, multiplier) VALUES ('AAPL','2026-10-30',150,'P',100)")
    conn.execute("INSERT INTO snapshots (contract_id, updated_at, bid, ask) VALUES (1, '2026-09-29T10:00:00', 1.0, 1.2)")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    snaps = SnapshotRepo(db).all()
    assert len(snaps) == 1 and snaps[0].bid == 1.0 and snaps[0].bid_size is None


def test_snapshot_bid_size_roundtrip_and_price_update(db):
    contracts, snaps, infos = ContractRepo(db), SnapshotRepo(db), TickerInfoRepo(db)
    c = OptionContract("AAPL", date(2026, 10, 30), 150.0)
    contracts.replace_for_ticker("AAPL", [c])
    snaps.upsert(ContractSnapshot(c, NOW, bid=1.0, ask=1.2, bid_size=37))
    assert snaps.all("AAPL")[0].bid_size == 37
    infos.upsert(TickerInfo("AAPL", sector="Tech", underlying_price=100.0, updated_daily_at=NOW))
    infos.update_price("AAPL", 91.5)
    got = infos.get("AAPL")
    assert got.underlying_price == 91.5 and got.updated_daily_at == NOW and got.sector == "Tech"
    infos.update_price("NOPE", 1.0)   # ticker sin ficha: no falla ni crea filas
    assert infos.get("NOPE") is None


def test_iv_bars_store_high_low_and_flag_old_rows_for_backfill(db):
    r = IVHistoryRepo(db)
    r.add("AAPL", [(date(2026, 9, 1), 0.20), (date(2026, 9, 2), 0.25, 0.30, 0.18)])   # una antigua, una nueva
    assert r.bars("AAPL") == [
        (date(2026, 9, 1), 0.20, None, None), (date(2026, 9, 2), 0.25, 0.30, 0.18)
    ]
    assert r.series("AAPL") == [(date(2026, 9, 1), 0.20), (date(2026, 9, 2), 0.25)]   # series sigue igual
    assert r.needs_hilo_backfill("AAPL", date(2026, 8, 1)) is True
    assert r.needs_hilo_backfill("AAPL", date(2026, 9, 2)) is False                    # ventana solo con la nueva
    r.add("AAPL", [(date(2026, 9, 1), 0.20, 0.22, 0.19)])                              # se rehace con máx/mín
    assert r.needs_hilo_backfill("AAPL", date(2026, 8, 1)) is False


def test_migration_from_v2_keeps_iv_history(tmp_path):
    import sqlite3

    from scanner_opciones.storage.db import MIGRATIONS

    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.executescript(MIGRATIONS[1])
    conn.execute("PRAGMA user_version = 2")
    conn.execute("INSERT INTO iv_history (ticker, day, iv) VALUES ('AAPL', '2026-09-01', 0.2)")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert IVHistoryRepo(db).bars("AAPL") == [(date(2026, 9, 1), 0.2, None, None)]


def test_migration_from_v3_adds_misses_and_margin_date(tmp_path):
    import sqlite3

    from scanner_opciones.storage.db import MIGRATIONS

    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    for script in MIGRATIONS[:3]:
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("INSERT INTO contracts (ticker, expiry, strike, right, multiplier) VALUES ('AAPL','2026-10-30',75,'P',100)")
    conn.execute("INSERT INTO snapshots (contract_id, updated_at, initial_margin) VALUES (1, '2026-09-29T10:00:00', 1500)")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    snap = SnapshotRepo(db).all("AAPL")[0]
    assert snap.initial_margin == 1500 and snap.margin_at is None      # sin fecha: el margen se pedirá de nuevo
    assert db.conn.execute("SELECT COUNT(*) FROM contract_misses").fetchone()[0] == 0
