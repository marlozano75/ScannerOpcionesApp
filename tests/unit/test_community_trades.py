"""Libro de operaciones del Feed de la comunidad (`community_trades`): upsert acumulativo, métricas de salida y estilo por trader."""
from datetime import date, datetime

from scanner_opciones.community.feed_data import FeedData, FeedLeg, FeedTrade
from scanner_opciones.jobs.community_trade_log import CommunityTradeLogJob
from scanner_opciones.storage.community_trades import CommunityTradeRepo
from scanner_opciones.storage.db import Database


def put(id_, trader, *, opened, closed=None, pnl=None, sell=1.5, close_price=None, strike=80, expiry="2026-11-20"):
    leg = FeedLeg("SELL", "P", 1, sell, strike, date.fromisoformat(expiry), close_price)
    return FeedTrade(id_, trader, "NFLX", "Short Put", "closed" if closed else "open",
                     date.fromisoformat(opened), date.fromisoformat(closed) if closed else None, pnl, "USD", None, (leg,))


def repo():
    return CommunityTradeRepo(Database(":memory:"))


def test_upsert_inserts_then_updates_on_close():
    r = repo()
    assert r.upsert_many([put("a", "Zalo", opened="2026-10-01")], "2026-10-02T10:00:00") == {"new": 1, "closed": 0, "updated": 0}
    # la misma operación, ahora cerrada: se actualiza en su sitio (no se duplica) y cuenta como cierre
    summary = r.upsert_many([put("a", "Zalo", opened="2026-10-01", closed="2026-10-16", pnl=90.0, close_price=0.6)], "2026-10-17T10:00:00")
    assert summary == {"new": 0, "closed": 1, "updated": 1}
    rows = r.all()
    assert len(rows) == 1 and rows[0].status == "closed" and rows[0].pnl == 90.0
    assert rows[0].pct_captured == 60.0 and rows[0].dte_close == 35 and rows[0].first_seen != rows[0].last_seen


def test_a_trade_that_leaves_the_window_is_kept():
    r = repo()
    r.upsert_many([put("a", "Zalo", opened="2026-10-01", closed="2026-10-16", pnl=90.0, close_price=0.6)], "2026-10-17T10:00:00")
    r.upsert_many([put("b", "Zalo", opened="2026-10-20")], "2026-10-21T10:00:00")      # otro día, otra tarjeta
    assert len(r.all()) == 2      # la primera sigue guardada aunque ya no venga en el Feed


def test_exit_styles_summarises_credit_closed_trades():
    r = repo()
    trades = [put(f"z{i}", "Zalo", opened="2026-10-01", closed="2026-10-16", pnl=90.0, close_price=0.6) for i in range(4)]
    trades += [put("open1", "Zalo", opened="2026-10-25")]                               # abierta: no cuenta
    trades += [put(f"k{i}", "Kike", opened="2026-10-01", closed="2026-10-05", pnl=50.0, close_price=1.0) for i in range(2)]   # pocas
    r.upsert_many(trades, "2026-10-26T10:00:00")
    styles = r.exit_styles(min_closed=3)
    assert [s.trader for s in styles] == ["Zalo"]       # Kike tiene menos de 3 cerradas
    z = styles[0]
    assert z.closed == 4 and z.wins == 4 and z.win_rate == 100.0
    assert z.median_dte_close == 35 and z.median_hold_days == 15 and z.median_pct_captured == 60.0
    assert z.strategies == ("Short Put",)


def test_job_only_ingests_a_new_stamp():
    r = repo()
    store = {"stamp": None}
    data = FeedData(datetime.fromisoformat("2026-10-31T13:00:00+02:00"), (put("a", "Zalo", opened="2026-10-01"),))
    job = CommunityTradeLogJob(r, lambda: data, lambda: store["stamp"], lambda s: store.update(stamp=s),
                               now=lambda: datetime(2026, 10, 31, 13, 0, 0))
    assert job.run()["new"] == 1
    assert job.run() == {"new": 0, "closed": 0, "updated": 0}      # mismo stamp: no reingiere
    assert job.run(force=True)["updated"] == 1                     # forzado: reingiere
