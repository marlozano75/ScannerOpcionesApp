"""SEC EDGAR: cocientes de solvencia y de calidad del flujo de caja (deuda, cobertura, efectivo, capex, activos, recompras)."""
from datetime import date

import pytest

from scanner_opciones.marketdata.edgar import parse_company_facts
from scanner_opciones.marketdata.financials import NO_LIMIT
from tests.unit.test_edgar import CAPEX, OCF, TODAY, flow, gaap, inst

# balance al 30-jun-2026: patrimonio 400, pasivo 600, activos 1000, efectivo 90
BASE = dict(
    Liabilities=[inst("2026-06-30", 600.0)], StockholdersEquity=[inst("2026-06-30", 400.0)],
    Assets=[inst("2026-06-30", 1000.0)], CashAndCashEquivalentsAtCarryingValue=[inst("2026-06-30", 90.0)],
    NetCashProvidedByUsedInOperatingActivities=OCF, PaymentsToAcquirePropertyPlantAndEquipment=CAPEX,   # TTM: 1200 y 250
)
OP = [flow("2025-01-01", "2025-12-31", 900.0), flow("2026-01-01", "2026-06-30", 500.0, "10-Q"), flow("2025-01-01", "2025-06-30", 300.0, "10-Q")]   # 1100
INT = [flow("2025-01-01", "2025-12-31", 80.0), flow("2026-01-01", "2026-06-30", 50.0, "10-Q"), flow("2025-01-01", "2025-06-30", 30.0, "10-Q")]     # 100


def parse(**extra):
    return parse_company_facts(gaap(**{**BASE, **extra}), TODAY)


def test_company_with_debt_gets_every_ratio():
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], ShortTermBorrowings=[inst("2026-06-30", 40.0)],
              DebtCurrent=[inst("2026-06-30", 60.0)], OperatingIncomeLoss=OP, InterestExpense=INT)
    assert f.debt_to_equity == pytest.approx(240 / 400)             # LongTermDebt + préstamos a corto plazo
    assert f.ocf_to_debt == pytest.approx(1200 / 240)
    assert f.interest_coverage == pytest.approx(1100 / 100)
    assert f.cash_to_short_debt == pytest.approx(90 / 60)           # DebtCurrent manda sobre la suma de partes
    assert f.capex_to_ocf == pytest.approx(250 / 1200)
    assert f.fcf_to_assets == pytest.approx(950 / 1000)


def test_debt_is_rebuilt_from_noncurrent_and_current_parts():
    f = parse(LongTermDebtNoncurrent=[inst("2026-06-30", 150.0)], LongTermDebtCurrent=[inst("2026-06-30", 50.0)])
    assert f.debt_to_equity == pytest.approx(200 / 400) and f.cash_to_short_debt == pytest.approx(90 / 50)


def test_other_debt_tags_count_when_there_is_no_long_term_debt_tag():
    f = parse(ConvertibleNotesPayable=[inst("2026-06-30", 120.0)], SeniorNotes=[inst("2026-06-30", 80.0)])
    assert f.debt_to_equity == pytest.approx(120 / 400)             # el mayor de los pagarés (no se suman: se solapan)


def test_no_debt_tags_with_a_credible_balance_means_no_debt():
    """ANET, CDNS, CPRT, DDOG…: no publican deuda porque no la tienen."""
    f = parse()                                                     # pasivo/patrimonio = 1,5 → no es < 1,5: dudoso
    assert f.debt_to_equity is None and f.ocf_to_debt is None
    f = parse(Liabilities=[inst("2026-06-30", 400.0)])              # 1,0 < 1,5: creíble
    assert f.debt_to_equity == 0.0
    assert f.interest_coverage == NO_LIMIT and f.ocf_to_debt == NO_LIMIT and f.cash_to_short_debt == NO_LIMIT


def test_interest_coverage_needs_both_figures_when_there_is_debt():
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP)
    assert f.interest_coverage is None                              # hay deuda pero no se sabe cuánto paga
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP, InterestExpense=[flow("2025-01-01", "2025-12-31", 0.0)])
    assert f.interest_coverage == NO_LIMIT                          # intereses cero
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=[flow("2025-01-01", "2025-12-31", -50.0)], InterestExpense=INT)
    assert f.interest_coverage < 0                                  # pérdidas operativas: no cubre


def test_cash_to_short_debt_without_current_debt_is_unlimited():
    f = parse(LongTermDebtNoncurrent=[inst("2026-06-30", 300.0)])
    assert f.cash_to_short_debt == NO_LIMIT


def test_capex_ratio_when_operating_cash_flow_is_not_positive():
    negative = [flow("2025-01-01", "2025-12-31", -100.0)]
    f = parse(NetCashProvidedByUsedInOperatingActivities=negative, PaymentsToAcquirePropertyPlantAndEquipment=[flow("2025-01-01", "2025-12-31", 20.0)])
    assert f.capex_to_ocf == NO_LIMIT and f.fcf_to_assets == pytest.approx(-120 / 1000)


def test_net_buyback_is_the_drop_in_diluted_shares_between_the_last_two_fiscal_years():
    def shares(*pairs):
        facts = [{"start": f"{y - 1}-01-01", "end": f"{y - 1}-12-31", "val": v, "form": "10-K", "filed": f"{y}-02-01"} for y, v in pairs]
        return {"facts": {"us-gaap": {**gaap(**BASE)["facts"]["us-gaap"],
                                      "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": facts}}}}}
    assert parse_company_facts(shares((2025, 1000.0), (2026, 970.0)), TODAY).net_buyback_pct == pytest.approx(3.0)    # −3 % de acciones
    assert parse_company_facts(shares((2025, 1000.0), (2026, 1050.0)), TODAY).net_buyback_pct == pytest.approx(-5.0)  # dilución
    assert parse_company_facts(shares((2026, 970.0)), TODAY).net_buyback_pct is None                                   # un solo año
    old = parse_company_facts(shares((2018, 1000.0), (2019, 900.0)), TODAY)
    assert old.net_buyback_pct is None                                                                               # datos viejos


def test_stale_balance_gives_no_ratios():
    old = {k: [dict(f, end="2019-06-30", **({"start": "2018-07-01"} if "start" in f else {})) for f in v] for k, v in BASE.items()}
    assert parse_company_facts(gaap(**old), TODAY) is None
