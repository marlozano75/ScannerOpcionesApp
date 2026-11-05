from datetime import date, timedelta

import pytest

from scanner_opciones.config.settings import CdsSettings, ClosingSettings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionChain, OptionContract
from scanner_opciones.scanner.candidates import call_candidate_contracts, is_monthly_expiry, on_strike_grid
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.premium import summarize_calls, summarize_puts

TODAY = date(2026, 11, 5)
CLOSING = ClosingSettings()
CRIT = ScanCriteria(strike_below_pct_min=10, strike_below_pct_max=30, min_yield_pct=1, dte_min=20, dte_max=40)


def snap(strike, days, bid, ask, oi, right=OptionRight.PUT):
    c = OptionContract("ZZZ", TODAY + timedelta(days=days), strike, right)
    mid = (bid + ask) / 2
    return ContractSnapshot(c, None, bid=bid, ask=ask, open_interest=oi, spread_pct=(ask - bid) / mid * 100)


def test_monthly_expiry_is_the_third_friday():
    assert is_monthly_expiry(date(2026, 12, 18)) and not is_monthly_expiry(date(2026, 12, 11)) and not is_monthly_expiry(date(2026, 12, 25))


def test_strike_grid():
    assert on_strike_grid(55, 5) and not on_strike_grid(57, 5) and on_strike_grid(12.5, 5) and on_strike_grid(57, 0)


def test_cds_calls_keep_only_monthlies_and_grid_strikes():
    monthly, weekly = date(2027, 6, 18), date(2027, 6, 25)
    chain = OptionChain("ZZZ", [monthly, weekly], [125.0, 127.0, 135.0])
    base = dict(dte_min=100, dte_max=600, long_otm_pct_min=20, long_otm_pct_max=40, width_options=(10.0,), width_pct_min=None, width_pct_max=None)
    got = {(c.expiry, c.strike) for c in call_candidate_contracts(chain, 100.0, TODAY, CdsSettings(**base, monthly_only=True))}
    assert got == {(monthly, 125.0), (monthly, 127.0), (monthly, 135.0)}
    got = {(c.expiry, c.strike) for c in call_candidate_contracts(chain, 100.0, TODAY, CdsSettings(**base, monthly_only=True, strike_multiple=5))}
    assert got == {(monthly, 125.0), (monthly, 135.0)}
    assert len(call_candidate_contracts(chain, 100.0, TODAY, CdsSettings(**base, monthly_only=False))) == 6


def test_puts_summary_counts_closable_contracts_in_the_window():
    snaps = [snap(80, 30, 1.0, 1.1, 500),      # 20 % bajo el precio, DTE 30, spread ~9,5 %, OI 500: cerrable
             snap(75, 30, 0.8, 1.2, 500),      # spread 40 %: no cerrable
             snap(85, 30, 2.0, 2.1, 10),       # 15 %: OI 10 → no cerrable
             snap(95, 30, 3.0, 3.1, 900),      # solo 5 % bajo el precio: fuera de la ventana
             snap(80, 10, 0.5, 0.55, 900)]     # DTE 10: fuera de la ventana (entra en la banda ancha)
    row = summarize_puts("ZZZ", "P", snaps, 100.0, TODAY, CRIT, CLOSING)
    assert (row.contracts, row.closable, row.max_oi) == (3, 1, 500)
    assert row.best_yield_pct == pytest.approx(max((b + 0.25 * (a - b)) / k * 100 for k, b, a in ((80, 1.0, 1.1), (75, 0.8, 1.2), (85, 2.0, 2.1))))
    assert row.best_closable_yield_pct == pytest.approx((1.0 + 0.025) / 80 * 100)
    assert row.best_ann_wide_pct >= row.best_yield_pct * 365 / 30 - 1e-9


def test_puts_summary_without_contracts_in_the_window_still_returns_a_row():
    row = summarize_puts("ZZZ", "P", [snap(95, 30, 3.0, 3.1, 900)], 100.0, TODAY, CRIT, CLOSING)
    assert (row.contracts, row.closable, row.best_yield_pct, row.max_oi) == (0, 0, None, None)


def test_calls_summary_uses_the_cds_window():
    cds = CdsSettings(dte_min=200, dte_max=500, long_otm_pct_min=25, long_otm_pct_max=130)
    snaps = [snap(130, 300, 1.0, 1.1, 200, OptionRight.CALL), snap(140, 300, 0.5, 0.9, 200, OptionRight.CALL), snap(105, 300, 5.0, 5.1, 900, OptionRight.CALL)]
    row = summarize_calls("ZZZ", snaps, 100.0, TODAY, cds, CLOSING)
    assert (row.profile, row.contracts, row.closable, row.best_yield_pct) == ("CDS", 2, 1, None)
