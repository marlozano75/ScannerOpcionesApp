"""JSON del Feed de la comunidad (list_community_trades): lectura tolerante."""
import json
from datetime import date

import pytest

from scanner_opciones.community.feed_data import FeedError, load_feed, parse_feed

OK = {
    "version": 1, "generated_at": "2026-10-31T13:00:00+02:00", "source": "tortuga",
    "trades": [
        {"id": "a1", "trader": "A.A.G", "ticker": "MSFT", "strategy": "Iron Condor", "status": "open",
         "opened": "2026-10-30", "closed": None, "pnl": None, "currency": "USD",
         "legs": "SELL 1 490P / BUY 1 485P", "note": None},
        {"id": "a2", "trader": "Kike", "ticker": "AMZN", "strategy": "Long Call", "status": "closed",
         "opened": "2026-10-30", "closed": "2026-10-30", "pnl": 83.42, "currency": "USD",
         "legs": "BUY 3 245C · vence 02/11", "note": "entro por el hueco"},
    ],
}


def text(**changes):
    return json.dumps({**OK, **changes})


def test_a_valid_feed_is_read():
    d = parse_feed(text())
    assert d.generated_at.isoformat() == "2026-10-31T13:00:00+02:00" and d.warnings == () and len(d.trades) == 2
    closed = d.trades[1]
    assert (closed.status, closed.pnl, closed.closed, closed.note) == ("closed", 83.42, date(2026, 10, 30), "entro por el hueco")
    assert d.trades[0].pnl is None and d.trades[0].note is None


@pytest.mark.parametrize("bad", ["no json", "[]", json.dumps({**OK, "version": 2}), json.dumps({**OK, "generated_at": "ayer"}),
                                 json.dumps({**OK, "trades": "x"})])
def test_a_file_that_is_not_this_format_is_rejected(bad):
    with pytest.raises(FeedError):
        parse_feed(bad)


def test_malformed_trades_are_skipped_and_reported():
    trades = [
        {"id": "ok", "trader": "A", "ticker": "KO", "strategy": "Short Put", "status": "open", "opened": "2026-10-30"},
        {"id": "x", "trader": "", "ticker": "KO", "status": "open", "opened": "2026-10-30"},            # sin trader
        {"id": "y", "trader": "A", "ticker": "KO", "status": "raro", "opened": "2026-10-30"},           # estado no válido
        {"id": "z", "trader": "A", "ticker": "KO", "status": "closed", "opened": "2026-10-30", "closed": "2026-10-01"},  # cierra antes de abrir
        {"id": "w", "trader": "A", "ticker": "KO", "status": "open", "opened": "2026-10-30", "pnl": "mucho"},            # pnl no numérico
    ]
    d = parse_feed(text(trades=trades))
    assert [t.id for t in d.trades] == ["ok"] and len(d.warnings) == 4
    assert d.trades[0].ticker == "KO" and d.trades[0].currency == ""


def test_ticker_with_share_class_is_normalised():
    d = parse_feed(text(trades=[{"id": "b", "trader": "A", "ticker": "BRK.B", "strategy": "Stock", "status": "open", "opened": "2026-10-30"}]))
    assert d.trades[0].ticker == "BRK-B"


def test_load_feed_reports_a_missing_file(tmp_path):
    with pytest.raises(FeedError, match="no existe"):
        load_feed(tmp_path / "nada.json")
    good = tmp_path / "feed.json"
    good.write_text(text(), encoding="utf-8")
    assert len(load_feed(good).trades) == 2
