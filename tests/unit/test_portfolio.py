from datetime import date, datetime

import pytest

from scanner_opciones.config.settings import CushionThresholds, Settings
from scanner_opciones.domain.enums import OperationType, OptionRight, TrafficLight
from scanner_opciones.domain.models import (
    AccountSummary, ContractSnapshot, OptionContract, Position, TickerInfo,
)
from scanner_opciones.portfolio.cushion import build_risk_status, classify, classify_severity, cushion_pct
from scanner_opciones.portfolio.diversification import (
    candidate_impact, nominal, sector_exposure, total_exposure, weekly_sector_exposure, week_start,
)
from scanner_opciones.portfolio.simulator import SimulatedTrade, simulate
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.engine import run_scan

T = CushionThresholds()
TODAY = date(2026, 9, 29)  # martes; semana empieza el 2026-09-28


def put(ticker, strike, expiry, qty=-1, sector="Tech"):
    return Position(ticker, qty, 0.0, sector, OptionContract(ticker, expiry, strike, OptionRight.PUT))


def stock(ticker, value, sector="Tech"):
    return Position(ticker, 100, value, sector)


class TestCushion:
    @pytest.mark.parametrize(
        "value,light",
        [
            (55, TrafficLight.GREEN), (40.01, TrafficLight.GREEN),
            (40, TrafficLight.AMBER), (35, TrafficLight.AMBER), (30.01, TrafficLight.AMBER),
            (30, TrafficLight.RED), (10, TrafficLight.RED), (-5, TrafficLight.RED),
            (None, TrafficLight.UNKNOWN),
        ],
    )
    def test_classify(self, value, light):
        assert classify(value, T) is light

    def test_cushion_pct(self):
        assert cushion_pct(45_000, 100_000) == 45
        assert cushion_pct(None, 100_000) is None
        assert cushion_pct(10, 0) is None
        assert cushion_pct(10, None) is None

    def test_build_risk_status(self):
        acc = AccountSummary(
            "DU1", net_liquidation=100_000, excess_liquidity=45_000, cushion_pct=44.0,
            look_ahead_excess=32_000, post_expiration_excess=20_000, highest_severity=0,
        )
        r = build_risk_status(acc, T)
        assert r.current.cushion_pct == 44.0  # el de IBKR, no el calculado (45)
        assert r.current.light is TrafficLight.GREEN
        assert r.look_ahead.light is TrafficLight.AMBER and r.look_ahead.cushion_pct == 32
        assert r.post_expiration.light is TrafficLight.RED
        assert r.severity.light is TrafficLight.GREEN

    def test_post_expiration_zero_is_no_data(self):
        acc = AccountSummary("DU1", net_liquidation=100_000, post_expiration_excess=0.0, cushion_pct=50.0)
        r = build_risk_status(acc, T)
        assert r.post_expiration.light is TrafficLight.UNKNOWN and r.post_expiration.cushion_pct is None

    def test_missing_ibkr_cushion_is_unknown_not_computed(self):
        acc = AccountSummary("DU1", net_liquidation=100_000, excess_liquidity=45_000)
        r = build_risk_status(acc, T)
        assert r.current.light is TrafficLight.UNKNOWN
        assert r.severity.light is TrafficLight.UNKNOWN and r.severity.level is None

    @pytest.mark.parametrize(
        "level,light",
        [(0, TrafficLight.GREEN), (1, TrafficLight.AMBER), (2, TrafficLight.ORANGE),
         (3, TrafficLight.RED), (7, TrafficLight.UNKNOWN), (None, TrafficLight.UNKNOWN)],
    )
    def test_severity_colors(self, level, light):
        assert classify_severity(level).light is light


class TestExposure:
    def test_ept_stocks_plus_short_put_nominal(self):
        pos = [stock("AAPL", 20_000), put("MSFT", 300, date(2026, 10, 16), qty=-2, sector="Tech")]
        assert nominal(pos[1]) == 300 * 100 * 2
        assert total_exposure(pos) == 20_000 + 60_000

    def test_long_puts_and_calls_ignored(self):
        long_put = put("AAPL", 100, date(2026, 10, 16), qty=+1)
        call = Position("AAPL", -1, 0, "Tech", OptionContract("AAPL", date(2026, 10, 16), 100, OptionRight.CALL))
        assert total_exposure([long_put, call]) == 0

    def test_sector_weights_sum_100(self):
        pos = [stock("AAPL", 30_000), stock("KO", 10_000, "Staples"),
               put("JNJ", 100, date(2026, 10, 16), sector="Health")]
        w = sector_exposure(pos).weights_pct
        assert sum(w.values()) == pytest.approx(100)
        assert w["Tech"] == pytest.approx(30_000 / 50_000 * 100)

    def test_empty_and_missing_sector(self):
        assert sector_exposure([]).weights_pct == {}
        assert "Sin sector" in sector_exposure([Position("X", 1, 1000.0)]).weights_pct

    def test_weekly_buckets(self):
        assert week_start(TODAY) == date(2026, 9, 28)
        pos = [
            put("A", 100, date(2026, 10, 2)),                 # semana 0
            put("B", 50, date(2026, 10, 9), sector="Health"),   # semana 1
            put("C", 10, date(2026, 10, 12)),                  # semana 2
            put("D", 10, date(2026, 12, 18)),                  # fuera de 5 semanas
            stock("AAPL", 99_999),                             # las acciones no cuentan
        ]
        weeks = weekly_sector_exposure(pos, TODAY, 5)
        assert len(weeks) == 5
        assert [w.total for w in weeks] == [10_000, 5_000, 1_000, 0, 0]
        assert weeks[1].weights_pct == {"Health": 100.0}
        assert weeks[3].weights_pct == {}
        assert weeks[0].week_start == date(2026, 9, 28)


class TestCandidateImpact:
    POS = [stock("AAPL", 50_000), put("MSFT", 100, date(2026, 10, 30), sector="Tech")]  # EPT 60_000

    def test_assignment_and_sector_increase(self):
        # candidato: strike 80 -> nominal 8_000 en Tech; Tech ahora = 60_000/60_000 = 100%
        i = candidate_impact(self.POS, "Tech", 80, 100, date(2026, 10, 30))
        assert i.nominal == 8_000
        assert i.assignment_pct_of_portfolio == pytest.approx(8_000 / 68_000 * 100)
        assert i.sector_weight_now_pct == pytest.approx(100)
        assert i.sector_weight_increase_pct == pytest.approx(0)

    def test_new_sector_increases(self):
        i = candidate_impact(self.POS, "Health", 80, 100, date(2026, 10, 30))
        assert i.sector_weight_now_pct == 0
        assert i.sector_weight_increase_pct == pytest.approx(8_000 / 68_000 * 100)

    def test_same_week_weight(self):
        # misma semana que la put de MSFT (nominal 10_000): 8_000 / (10_000 + 8_000)
        i = candidate_impact(self.POS, "Tech", 80, 100, date(2026, 10, 28))
        assert i.week_weight_pct == pytest.approx(8_000 / 18_000 * 100)
        j = candidate_impact(self.POS, "Tech", 80, 100, date(2026, 11, 20))
        assert j.week_weight_pct == 100

    def test_empty_portfolio(self):
        i = candidate_impact([], None, 50, 100, date(2026, 10, 30))
        assert i.sector == "Sin sector" and i.assignment_pct_of_portfolio == 100
        assert i.sector_weight_increase_pct == pytest.approx(100)


class TestSimulator:
    ACC = AccountSummary("DU1", net_liquidation=100_000, excess_liquidity=50_000, cushion_pct=50.0)

    def trade(self, strike=80, qty=1, margin=2_000.0, sector="Health"):
        c = OptionContract("JNJ", date(2026, 10, 30), strike)
        return SimulatedTrade(c, qty, sector, margin)

    def test_before_after_and_deltas(self):
        pos = [stock("AAPL", 20_000)]
        r = simulate(pos, [self.trade(strike=80, qty=1)], self.ACC, T, TODAY)
        assert r.before.weights_pct == {"Tech": 100.0}
        assert r.after.total_exposure == 28_000
        assert r.after.weights_pct["Health"] == pytest.approx(8_000 / 28_000 * 100)
        assert r.delta_pct["Tech"] < 0 < r.delta_pct["Health"]
        assert sum(r.after.weights_pct.values()) == pytest.approx(100)
        assert r.added_nominal == 8_000

    def test_margin_and_cushion_approximation(self):
        r = simulate([], [self.trade(margin=2_000), self.trade(strike=70, margin=1_000)], self.ACC, T, TODAY)
        assert r.added_margin == 3_000 and r.margin_complete and r.margin_is_approximate
        assert r.cushion_before.cushion_pct == 50 and r.cushion_before.light is TrafficLight.GREEN
        assert r.cushion_after.cushion_pct == 47

    def test_cushion_drops_to_red(self):
        r = simulate([], [self.trade(margin=25_000)], self.ACC, T, TODAY)
        assert r.cushion_after.cushion_pct == 25 and r.cushion_after.light is TrafficLight.RED

    def test_missing_margin_flagged(self):
        r = simulate([], [self.trade(margin=None)], self.ACC, T, TODAY)
        assert not r.margin_complete and r.added_margin == 0

    def test_no_account(self):
        r = simulate([], [self.trade()], None, T, TODAY)
        assert r.cushion_before is None and r.cushion_after is None

    def test_no_trades_is_identity(self):
        pos = [stock("AAPL", 1_000)]
        r = simulate(pos, [], self.ACC, T, TODAY)
        assert r.before == r.after and r.added_nominal == 0

    def test_invalid_quantity(self):
        with pytest.raises(ValueError):
            simulate([], [self.trade(qty=0)], self.ACC, T, TODAY)

    def test_weeks_after_include_trade(self):
        r = simulate([], [self.trade()], self.ACC, T, TODAY)
        assert sum(w.total for w in r.weeks_before) == 0
        assert sum(w.total for w in r.weeks_after) == 8_000


class TestScanEngine:
    NOW = datetime(2026, 9, 29, 12)

    def snap(self, ticker, strike, y, ann):
        c = OptionContract(ticker, date(2026, 10, 30), strike)   # DTE 31
        price = y * strike / 100          # bid = ask: todas las referencias dan el mismo precio
        return ContractSnapshot(c, self.NOW, bid=price, ask=price, yield_pct=y, yield_annualized_pct=ann,
                                open_interest=100, spread_pct=5)

    def test_filters_enriches_and_sorts(self):
        crit = criteria_from_settings(Settings(), OperationType.REGULAR)
        infos = {"AAPL": TickerInfo("AAPL", sector="Tech", underlying_price=100.0),
                 "KO": TickerInfo("KO", sector="Staples", underlying_price=100.0)}
        snaps = [
            self.snap("AAPL", 78, 1.2, 14.0),
            self.snap("KO", 77, 1.5, 17.0),
            self.snap("AAPL", 78, 0.5, 6.0),   # yield insuficiente (mismo contrato distinto snapshot)
            self.snap("ZZZ", 78, 2.0, 24.0),   # sin info de precio
        ]
        out = run_scan(snaps, infos, [], crit, TODAY, include_rejections=True)
        assert [r.snapshot.contract.ticker for r in out.results] == ["KO", "AAPL"]  # orden por yield anualizado
        assert out.rejected_count == 2 and len(out.rejections) == 2
        first = out.results[0]
        assert first.dte == 31 and first.strike_distance_pct == pytest.approx(23)
        assert first.impact.assignment_pct_of_portfolio == 100

    def test_no_results(self):
        crit = criteria_from_settings(Settings(), OperationType.TACTICAL)
        out = run_scan([self.snap("AAPL", 78, 2, 20)], {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}, [], crit, TODAY)
        assert out.results == [] and out.rejected_count == 1
