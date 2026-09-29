import math
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from scanner_opciones.broker import ibkr_mapper as m
from scanner_opciones.config.settings import AccountTags

NOW = datetime(2026, 9, 29, 10)


def av(tag, value, currency="BASE", account="DU1"):
    return NS(tag=tag, value=value, currency=currency, account=account)


@pytest.mark.parametrize("x,expected", [(None, None), (math.nan, None), (-1, None), ("abc", None), (0, 0.0), ("2.5", 2.5)])
def test_num(x, expected):
    assert m.num(x) == expected


def test_expiry_roundtrip():
    assert m.parse_expiry("20261030") == date(2026, 10, 30)
    assert m.format_expiry(date(2026, 10, 30)) == "20261030"
    with pytest.raises(ValueError):
        m.parse_expiry("202610")


class TestAccountValues:
    def test_prefers_base_currency(self):
        vals = [av("NetLiquidation", "90", "EUR"), av("NetLiquidation", "100", "BASE")]
        assert m.pick_account_value(vals, "NetLiquidation") == 100

    def test_segment_suffix_fallback(self):
        assert m.pick_account_value([av("ExcessLiquidity-S", "55")], "ExcessLiquidity") == 55

    def test_filters_by_account_and_missing(self):
        vals = [av("NetLiquidation", "100", account="DU2")]
        assert m.pick_account_value(vals, "NetLiquidation", "DU1") is None
        assert m.pick_account_value([], "X") is None

    def test_non_numeric_skipped(self):
        assert m.pick_account_value([av("Cushion", "n/a")], "Cushion") is None

    def test_build_summary_uses_ibkr_cushion_and_severity(self):
        vals = [av("NetLiquidation", "100000"), av("ExcessLiquidity", "45000"), av("Cushion", "0.4523", ""),
                av("LookAheadExcessLiquidity", "40000"), av("PostExpirationExcess", "30000"),
                av("HighestSeverity", "2", "")]
        s = m.build_account_summary(vals, AccountTags(), "DU1", NOW)
        assert (s.net_liquidation, s.excess_liquidity, s.look_ahead_excess) == (100000, 45000, 40000)
        assert s.cushion_pct == pytest.approx(45.23)  # IBKR da una fracción
        assert s.post_expiration_excess == 30000 and s.highest_severity == 2
        assert s.updated_at == NOW

    def test_severity_zero_is_kept_and_missing_is_none(self):
        s0 = m.build_account_summary([av("HighestSeverity", "0", "")], AccountTags(), "DU1", NOW)
        assert s0.highest_severity == 0
        s = m.build_account_summary([av("NetLiquidation", "1")], AccountTags(), "DU1", NOW)
        assert s.highest_severity is None and s.cushion_pct is None

    def test_custom_tags(self):
        tags = AccountTags(highest_severity="MiTag")
        s = m.build_account_summary([av("MiTag", "3")], tags, "DU1", NOW)
        assert s.highest_severity == 3


def chain(exchange="SMART", tc="AAPL", mult="100", n=3):
    return NS(exchange=exchange, tradingClass=tc, multiplier=mult, expirations=["20261030"] * n)


def test_pick_chain_prefers_smart_class_and_multiplier():
    best = chain()
    got = m.pick_chain([chain("CBOE"), chain(tc="AAPL1", n=9), best, chain(mult="10")], "AAPL")
    assert got is best
    assert m.pick_chain([], "AAPL") is None


@pytest.mark.parametrize("v,exp", [("1234.5", 1234.5), ("", None), (None, None), (str(1.7976931348623157e308), None)])
def test_margin_change(v, exp):
    assert m.parse_margin_change(v) == exp
