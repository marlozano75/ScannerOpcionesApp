from datetime import date, datetime

from scanner_opciones.app import portfolio_cache
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import AccountSummary, OptionContract, Position


def _data():
    account = AccountSummary("U1", net_liquidation=38000.0, cushion_pct=42.0, updated_at=datetime(2026, 10, 28, 10, 0),
                             base_currency="EUR", usd_per_base=1.12)
    option = OptionContract("AAPL", date(2026, 12, 18), 150.0, OptionRight.PUT, 100, 123)
    positions = [Position("AAPL", -1, -250.0, "Tecnología", option, 180.0), Position("MSFT", 10, 4000.0)]
    return account, positions


def test_round_trip():
    account, positions = _data()
    raw = portfolio_cache.dump("paper", account, positions)
    assert portfolio_cache.load(raw, "paper") == (account, positions)


def test_otro_modo_o_dañado_no_se_usa():
    account, positions = _data()
    raw = portfolio_cache.dump("paper", account, positions)
    assert portfolio_cache.load(raw, "live") == (None, [])
    assert portfolio_cache.load("{no json", "paper") == (None, [])
    assert portfolio_cache.load(None, "paper") == (None, [])


def test_cada_modo_conserva_su_cache():
    account, positions = _data()
    raw = portfolio_cache.dump("paper", account, positions)
    raw = portfolio_cache.dump("live", account, positions[:1], raw)
    assert portfolio_cache.load(raw, "paper") == (account, positions)
    assert portfolio_cache.load(raw, "live") == (account, positions[:1])


def test_formato_antiguo_sigue_valiendo():
    import json
    account, positions = _data()
    new = json.loads(portfolio_cache.dump("paper", account, positions))["modes"]["paper"]
    old = json.dumps({"mode": "paper", **new})
    assert portfolio_cache.load(old, "paper") == (account, positions)
    raw = portfolio_cache.dump("live", account, positions[:1], old)
    assert portfolio_cache.load(raw, "paper") == (account, positions)
