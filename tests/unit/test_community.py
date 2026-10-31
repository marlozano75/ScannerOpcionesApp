"""JSON de la comunidad de Tortuga: lectura tolerante y comprobador de la rutina diaria."""
import json
import subprocess
import sys
from datetime import date

import pytest

from scanner_opciones.community.data import CommunityError, load_community, parse_community

OK = {
    "version": 1, "generated_at": "2026-10-12T13:00:00+02:00",
    "tickers": [
        {"ticker": "pep", "month_traders": 17, "month_operations": 45, "week_traders": 6, "week_operations": 10, "traders": ["JBJ", "yors37"]},
        {"ticker": "BRK.B", "month_traders": 4, "month_operations": 5},
    ],
    "ideas": [
        {"date": "2026-10-09", "trader": "Juancm", "ticker": "VZ", "strategy": "Short Put", "expiry": "2026-11-20",
         "short_strike": 42, "long_strike": None, "contracts": 1, "credit": 0.96, "max_loss": 4104},
        {"date": "2026-10-09", "trader": "GSAA", "ticker": "PEP", "strategy": "Bull Put Spread", "expiry": "2026-11-20",
         "short_strike": 120, "long_strike": 115, "contracts": 1, "credit": 0.75},
    ],
}


def text(**changes):
    return json.dumps({**OK, **changes})


def test_a_valid_file_is_read_and_tickers_are_normalised():
    d = parse_community(text())
    assert d.generated_at.isoformat() == "2026-10-12T13:00:00+02:00" and d.warnings == ()
    pep = d.by_ticker()["PEP"]
    assert (pep.month_traders, pep.week_traders, pep.traders) == (17, 6, ("JBJ", "yors37"))
    assert "BRK-B" in d.by_ticker() and d.by_ticker()["BRK-B"].week_traders == 0          # como los escribe la aplicación
    spread = d.ideas[1]
    assert (spread.short_strike, spread.long_strike, spread.day) == (120.0, 115.0, date(2026, 10, 9)) and spread.max_loss is None


@pytest.mark.parametrize("bad", ["no json", "[]", json.dumps({**OK, "version": 3}), json.dumps({**OK, "generated_at": "ayer"}),
                                 json.dumps({**OK, "tickers": "x"})])
def test_a_file_that_is_not_this_format_is_rejected(bad):
    with pytest.raises(CommunityError):
        parse_community(bad)


def test_version_2_with_trader_profiles_is_read():
    profiles = {
        "yors37": {"rank": 66, "tier": "Gold", "score": 49.9, "twr": 25.13, "win_rate": 73.8,
                   "profit_factor": 1.24, "expectancy": 19.24, "total_pnl": 7271.9, "capital_return": 5.25, "trades": 378},
        "Tradingopc": {"tier": "bronze", "profit_factor": 0.67, "expectancy": -35.2},   # pierde dinero aunque acierte mucho
    }
    d = parse_community(text(version=2, profiles=profiles))
    assert d.warnings == ()
    p = d.profile("YORS37")                                   # sin distinguir mayúsculas
    assert (p.tier, p.rank, p.profit_factor, p.trades) == ("gold", 66, 1.24, 378)
    assert d.profile("tradingopc").expectancy == -35.2 and d.profile("nadie") is None


def test_version_1_file_has_no_profiles():
    d = parse_community(text())                               # un JSON de versión 1 sigue siendo válido
    assert d.profiles == {} and d.profile("yors37") is None


def test_a_malformed_profile_is_skipped_and_reported():
    profiles = {"ok": {"tier": "gold", "profit_factor": 1.1}, "mal": {"profit_factor": "alto"}, "feo": "x"}
    d = parse_community(text(version=2, profiles=profiles))
    assert set(d.profiles) == {"ok", "mal"} and d.profile("mal").profit_factor is None    # el campo malo se descarta, el perfil no
    assert d.profile("feo") is None and any("feo" in w for w in d.warnings)


def test_malformed_items_are_skipped_and_reported():
    items = [{"ticker": "AAPL", "month_traders": 3, "month_operations": 4}, {"ticker": "???", "month_traders": 1, "month_operations": 1},
             {"ticker": "MSFT", "month_traders": -1, "month_operations": 1}, "x"]
    ideas = [{"date": "2026-10-09", "trader": "a", "ticker": "VZ", "strategy": "Long Call", "expiry": "2026-11-20", "short_strike": 40, "credit": 1},
             {"date": "2026-10-09", "trader": "a", "ticker": "VZ", "strategy": "Short Put", "expiry": "2026-11-20", "short_strike": 40,
              "long_strike": 35, "credit": 1},                                           # un Short Put no lleva pata comprada
             {"date": "2026-10-09", "trader": "a", "ticker": "VZ", "strategy": "Bull Put Spread", "expiry": "2026-11-20", "short_strike": 40,
              "long_strike": 45, "credit": 1},                                           # la comprada tiene que estar por debajo
             {"date": "2026-10-09", "trader": "a", "ticker": "VZ", "strategy": "Short Put", "expiry": "2026-09-01", "short_strike": 40, "credit": 1}]
    d = parse_community(text(tickers=items, ideas=ideas))
    assert [t.ticker for t in d.tickers] == ["AAPL"] and d.ideas == () and len(d.warnings) == 3 + 4


def test_load_community_reports_a_missing_file_and_the_checker_exit_codes(tmp_path):
    with pytest.raises(CommunityError, match="no existe"):
        load_community(tmp_path / "nada.json")
    good, bad, empty = tmp_path / "good.json", tmp_path / "bad.json", tmp_path / "empty.json"
    good.write_text(text(), encoding="utf-8")
    bad.write_text("{", encoding="utf-8")
    empty.write_text(text(tickers=[]), encoding="utf-8")

    def run(path):
        return subprocess.run([sys.executable, "-m", "scanner_opciones.community", str(path)], capture_output=True, text=True, encoding="utf-8")

    assert run(good).returncode == 0 and "OK: 2 tickers, 2 ideas" in run(good).stdout
    assert run(bad).returncode == 1 and run(empty).returncode == 1
