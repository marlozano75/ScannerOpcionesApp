"""Fase 1 de visibilidad: cierres propios en Ideas, margen comprometido en el Panel y resumen técnico del Scanner."""
from datetime import date, timedelta

from scanner_opciones.community.data import CommunityIdea
from scanner_opciones.community.ideas import past_trade
from scanner_opciones.config.settings import ManagementSettings, StrategySettings, TechnicalSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import OptionContract, Position
from scanner_opciones.portfolio.management import manage_puts, put_key
from scanner_opciones.portfolio.openings import parse_openings
from scanner_opciones.scanner.tech_summary import TechSummarizer

TODAY = date(2026, 10, 17)
EXP = date(2026, 11, 20)


def idea(ticker="VZ", strike=40.0):
    return CommunityIdea(date(2026, 10, 16), "ana", ticker, "Short Put", EXP, strike, None, 1, 0.5)


def closes(rows):
    return parse_openings({"version": 1, "generated_at": "2026-10-17T13:00:00+02:00", "puts": [], "closes": rows}).closes


def test_closes_are_parsed_and_bad_rows_warned():
    data = parse_openings({"version": 1, "generated_at": "2026-10-17T13:00:00+02:00", "puts": [],
                           "closes": [{"date": "2026-10-15", "ticker": "VZ", "expiry": "2026-11-20", "strike": 40, "pct_captured": 62.2, "pnl": 24.5},
                                      {"date": "x", "ticker": "Y"}]})
    assert len(data.closes) == 1 and data.closes[0].pct_captured == 62.2 and len(data.warnings) == 1


def test_past_trade_prefers_the_same_contract_and_respects_the_window():
    cs = closes([{"date": "2026-10-15", "ticker": "VZ", "expiry": "2026-12-18", "strike": 38, "pct_captured": 50},
                 {"date": "2026-10-01", "ticker": "VZ", "expiry": "2026-11-20", "strike": 40, "pct_captured": 70}])
    t = past_trade(idea(), cs, TODAY, 45)
    assert t.same_contract and t.days_ago == 16 and t.pct_captured == 70
    assert not past_trade(idea(strike=41), cs, TODAY, 45).same_contract
    assert past_trade(idea(), cs, TODAY, 1) is None and past_trade(idea("AAPL"), cs, TODAY, 45) is None


def put(ticker, strike, qty, avg_cost, value, hedge=None):
    o = OptionContract(ticker=ticker, expiry=EXP, strike=strike, right=OptionRight.PUT, multiplier=100)
    return Position(ticker, qty, value, option=o, avg_cost=avg_cost, hedge_strike=hedge)


def test_margin_is_what_if_for_naked_puts_and_max_loss_for_spreads():
    naked = put("VZ", 40, -2, -50.0, -30.0)
    spread_short = put("PEP", 100, -1, -150.0, -100.0)
    spread_long = put("PEP", 95, 1, 50.0, 40.0)
    rep = manage_puts([naked, spread_short, spread_long], 100_000, TODAY, {}, ManagementSettings(),
                      margins={put_key(naked): 700.0})
    by = {s.ticker: s for s in rep.puts}
    assert by["VZ"].margin == 1400.0 and not by["VZ"].margin_exact
    assert by["PEP"].margin is not None and by["PEP"].margin_exact and 0 < by["PEP"].margin < 500
    assert rep.margin_total == by["VZ"].margin + by["PEP"].margin and rep.margin_missing == 0
    assert manage_puts([naked], 100_000, TODAY, {}, ManagementSettings()).margin_missing == 1


def test_technical_summary_counts_measured_checks_only():
    days = [TODAY - timedelta(days=i) for i in range(300, 0, -1)]
    rising = [(d, 50 + i * 0.1) for i, d in enumerate(days)]
    s = TechSummarizer({"UP": rising}, TechnicalSettings(), StrategySettings(), TODAY).summary("UP", rising[-1][1], 60)
    labels = {c.short: c.ok for c in s.checks}
    assert labels["200"] is True and labels["T"] is True and s.total == 6 - sum(1 for v in labels.values() if v is None)
    assert "✓" in s.detail
    short = TechSummarizer({"N": rising[-30:]}, TechnicalSettings(), StrategySettings(), TODAY).summary("N", 53.0, 50)
    assert {c.short: c.ok for c in short.checks}["200"] is None
    assert TechSummarizer({}, TechnicalSettings(), StrategySettings(), TODAY).summary("X", None, 1) is None
