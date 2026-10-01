from datetime import date, datetime

import pytest

from scanner_opciones.config.settings import CandidateRange, Settings
from scanner_opciones.domain.enums import OperationType, OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionChain, OptionContract
from scanner_opciones.scanner.candidates import candidate_contracts
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.filters import reject_reason

TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 12, 0)
BASE = criteria_from_settings(Settings())
# los antiguos perfiles Regular y Táctica, ahora solo rangos concretos del mismo filtro
REGULAR = BASE.with_filters(strike_below_pct_min=20, strike_below_pct_max=40, dte_min=25, dte_max=35)
TACTICAL = BASE.with_filters(strike_below_pct_min=10, strike_below_pct_max=40, dte_min=1, dte_max=15)


def snap(strike=78.0, days=30, yield_pct=1.2, oi=500, spread=5.0, right=OptionRight.PUT):
    from datetime import timedelta
    c = OptionContract("AAPL", TODAY + timedelta(days=days), strike, right)
    # el yield objetivo se traduce a un bid = ask (spread 0): bid, mid y bid+X % dan el mismo precio
    price = None if yield_pct is None else yield_pct * strike / 100
    return ContractSnapshot(c, NOW, bid=price, ask=price, open_interest=oi, spread_pct=spread, yield_pct=yield_pct)


def test_criteria_from_settings():
    # filtro único: descuento 10–30 %, DTE 1–35 (todo editable en el formulario)
    assert (BASE.strike_below_pct_min, BASE.strike_below_pct_max) == (10, 30)
    assert (BASE.dte_min, BASE.dte_max) == (1, 35) and BASE.min_yield_pct == 1.0
    assert (BASE.regular_dte_min, BASE.regular_dte_max) == (25, 35)


@pytest.mark.parametrize("dte, expected", [
    (1, OperationType.TACTICAL), (24, OperationType.TACTICAL), (25, OperationType.REGULAR),
    (30, OperationType.REGULAR), (35, OperationType.REGULAR), (36, OperationType.TACTICAL), (60, OperationType.TACTICAL),
])
def test_operation_is_regular_between_25_and_35_dte_and_tactical_otherwise(dte, expected):
    assert BASE.operation_for(dte) is expected


def test_regular_passes():
    assert reject_reason(snap(), 100.0, TODAY, REGULAR) is None


@pytest.mark.parametrize(
    "kwargs,why",
    [
        (dict(days=24), "DTE"),
        (dict(days=36), "DTE"),
        (dict(strike=76.0), "strike"),   # 24% -> pasa, ver abajo
        (dict(strike=90.0), "strike"),   # 10% < 20% mínimo
        (dict(strike=50.0), "strike"),   # 50% > 40% máximo guardado
        (dict(yield_pct=0.99), "yield"),
        (dict(yield_pct=None), "yield"),
        (dict(right=OptionRight.CALL), "put"),
    ],
)
def test_regular_rejections(kwargs, why):
    if kwargs == dict(strike=76.0):
        assert reject_reason(snap(**kwargs), 100.0, TODAY, REGULAR) is None
        return
    assert why in reject_reason(snap(**kwargs), 100.0, TODAY, REGULAR)


def test_boundaries_are_inclusive():
    assert reject_reason(snap(strike=80.0, days=25, yield_pct=1.0), 100.0, TODAY, REGULAR) is None  # 20%, DTE min
    assert reject_reason(snap(strike=60.0, days=35, yield_pct=1.0), 100.0, TODAY, REGULAR) is None  # 40%, DTE max


def test_no_underlying_price():
    assert "precio" in reject_reason(snap(), None, TODAY, REGULAR)


def test_tactical():
    ok = snap(strike=90.0, days=10)
    assert reject_reason(ok, 100.0, TODAY, TACTICAL) is None
    assert "DTE" in reject_reason(snap(strike=90.0, days=16), 100.0, TODAY, TACTICAL)
    assert "strike" in reject_reason(snap(strike=95.0, days=10), 100.0, TODAY, TACTICAL)   # 5 % < 10 %
    assert reject_reason(snap(strike=70.0, days=10), 100.0, TODAY, TACTICAL) is None         # 30 % >= 10 %


class TestOptionalFilters:
    def test_min_oi(self):
        c = REGULAR.with_filters(min_oi=100)
        assert reject_reason(snap(oi=100), 100.0, TODAY, c) is None
        assert "OI" in reject_reason(snap(oi=99), 100.0, TODAY, c)
        assert "OI" in reject_reason(snap(oi=None), 100.0, TODAY, c)

    def test_max_spread(self):
        c = REGULAR.with_filters(max_spread_pct=10)
        assert reject_reason(snap(spread=10), 100.0, TODAY, c) is None
        assert "spread" in reject_reason(snap(spread=10.5), 100.0, TODAY, c)
        assert "spread" in reject_reason(snap(spread=None), 100.0, TODAY, c)

    def test_iv_rank_and_percentile(self):
        c = REGULAR.with_filters(min_iv_rank=30, min_iv_percentile=40)
        assert reject_reason(snap(), 100.0, TODAY, c, iv_rank=30, iv_percentile=40) is None
        assert "Rank" in reject_reason(snap(), 100.0, TODAY, c, iv_rank=29, iv_percentile=90)
        assert "Percentile" in reject_reason(snap(), 100.0, TODAY, c, iv_rank=50, iv_percentile=39)
        assert "Rank" in reject_reason(snap(), 100.0, TODAY, c)

    def test_disabled_filters_ignore_missing_data(self):
        assert reject_reason(snap(oi=None, spread=None), 100.0, TODAY, REGULAR) is None


class TestCandidates:
    CHAIN = OptionChain(
        "AAPL",
        expiries=[date(2026, 10, 9), date(2026, 10, 30), date(2026, 12, 18)],  # DTE 10, 31, 80
        strikes=[50, 55, 70, 75, 85, 86, 90, 100],
    )

    def test_stored_range(self):
        got = candidate_contracts(self.CHAIN, 100.0, TODAY, CandidateRange())   # 5-40 %, DTE 1-45
        pairs = {(c.expiry, c.strike) for c in got}
        expected_strikes = {70, 75, 85, 86, 90}                                # -30, -25, -15, -14, -10 %
        assert pairs == {(e, k) for e in (date(2026, 10, 9), date(2026, 10, 30)) for k in expected_strikes}
        assert all(c.right is OptionRight.PUT for c in got)                    # DTE 80 queda fuera

    def test_custom_range(self):
        rng = CandidateRange(strike_below_pct_min=10, strike_below_pct_max=15, dte_min=20, dte_max=40)
        got = candidate_contracts(self.CHAIN, 100.0, TODAY, rng)
        assert {(c.expiry, c.strike) for c in got} == {(date(2026, 10, 30), k) for k in (85, 86, 90)}

    def test_no_price(self):
        assert candidate_contracts(self.CHAIN, 0, TODAY, CandidateRange()) == []
        assert candidate_contracts(self.CHAIN, None, TODAY, CandidateRange()) == []


class TestPriceReference:
    """Con un spread ancho, el precio de referencia cambia el yield y por tanto el filtro."""

    def wide(self):
        # bid 0.50 / ask 1.10 sobre strike 78 -> bid 0.64 %, bid+25 % 0.83 %, mid 1.03 %
        c = OptionContract("AAPL", TODAY + __import__("datetime").timedelta(days=30), 78.0)
        return ContractSnapshot(c, NOW, bid=0.50, ask=1.10, open_interest=500, spread_pct=75.0)

    def with_ref(self, mode, x=25.0):
        return REGULAR.with_filters(price_reference=mode, price_spread_pct=x, min_yield_pct=1.0)

    def test_only_mid_reaches_one_percent(self):
        s = self.wide()
        assert "yield" in reject_reason(s, 100.0, TODAY, self.with_ref(PriceReference.BID))
        assert "yield" in reject_reason(s, 100.0, TODAY, self.with_ref(PriceReference.BID_PLUS_SPREAD, 25))
        assert reject_reason(s, 100.0, TODAY, self.with_ref(PriceReference.MID)) is None

    def test_bid_plus_spread_threshold_depends_on_x(self):
        s = self.wide()
        # 0.50 + x * 0.60 >= 0.78  ->  x >= 46.7 %
        assert "yield" in reject_reason(s, 100.0, TODAY, self.with_ref(PriceReference.BID_PLUS_SPREAD, 45))
        assert reject_reason(s, 100.0, TODAY, self.with_ref(PriceReference.BID_PLUS_SPREAD, 50)) is None

    def test_invalid_quote_is_rejected_for_every_reference(self):
        c = OptionContract("AAPL", TODAY + __import__("datetime").timedelta(days=30), 78.0)
        bad = ContractSnapshot(c, NOW, bid=-1, ask=1.1, open_interest=500, spread_pct=None)
        for mode in PriceReference:
            assert "yield" in reject_reason(bad, 100.0, TODAY, self.with_ref(mode))
