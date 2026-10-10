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


# ---- ROIC: resultado operativo tras impuestos / (deuda financiera + patrimonio) -----------------------------
TAX = [flow("2025-01-01", "2025-12-31", 180.0), flow("2026-01-01", "2026-06-30", 100.0, "10-Q"), flow("2025-01-01", "2025-06-30", 60.0, "10-Q")]       # 220
PRETAX = [flow("2025-01-01", "2025-12-31", 900.0), flow("2026-01-01", "2026-06-30", 500.0, "10-Q"), flow("2025-01-01", "2025-06-30", 300.0, "10-Q")]    # 1100
PRETAX_TAG = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"


def test_roic_uses_the_effective_tax_rate_and_debt_plus_equity_as_invested_capital():
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP, IncomeTaxExpenseBenefit=TAX, **{PRETAX_TAG: PRETAX})
    # operativo 1100 × (1 − 220/1100 = 20 %) = 880 sobre capital 200 + 400
    assert f.roic == pytest.approx(880 / 600)


def test_roic_falls_back_to_the_statutory_rate_when_the_effective_one_is_absurd():
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP)               # sin impuestos: 21 %
    assert f.roic == pytest.approx(1100 * 0.79 / 600)
    negative_tax = [flow("2025-01-01", "2025-12-31", -50.0)]
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP, IncomeTaxExpenseBenefit=negative_tax,
              **{PRETAX_TAG: [flow("2025-01-01", "2025-12-31", 1000.0)]})
    assert f.roic == pytest.approx(1100 * 0.79 / 600)                                         # tasa negativa: se descarta


def test_roic_without_debt_tags_on_a_credible_balance_is_operating_profit_over_equity():
    f = parse(Liabilities=[inst("2026-06-30", 400.0)], OperatingIncomeLoss=OP)               # sin deuda creíble
    assert f.roic == pytest.approx(1100 * 0.79 / 400)


def test_operating_losses_give_a_negative_roic_without_a_tax_shield():
    f = parse(LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=[flow("2025-01-01", "2025-12-31", -300.0)])
    assert f.roic == pytest.approx(-300 / 600)


def test_roic_needs_operating_income_and_positive_capital():
    assert parse(LongTermDebt=[inst("2026-06-30", 200.0)]).roic is None                       # sin resultado operativo
    f = parse(StockholdersEquity=[inst("2026-06-30", -300.0)], LongTermDebt=[inst("2026-06-30", 200.0)], OperatingIncomeLoss=OP)
    assert f.roic is None                                                                     # capital invertido ≤ 0


# ---- estabilidad de los beneficios: años con pérdidas de los últimos 10 años fiscales ------------------------
def annual(profits, last_year=2025, tag="NetIncomeLoss"):
    """Beneficio neto anual (10-K) de los años que terminan en `last_year`, `last_year − 1`…, del más reciente al más antiguo."""
    return {tag: [flow(f"{last_year - i}-01-01", f"{last_year - i}-12-31", v) for i, v in enumerate(profits)]}


def test_loss_years_counts_the_fiscal_years_with_losses_among_the_last_ten():
    f = parse(**annual([50, 40, -5, 30, 20, 10, 9, -2, 8, 7, -100, -100]))                    # 12 años: solo cuentan los 10 últimos
    assert (f.loss_years, f.fiscal_years) == (2, 10)


def test_a_company_with_losses_every_year_in_a_short_history_is_still_judged_after_five_years():
    assert parse(**annual([5, 4, 3, 2])).loss_years is None and parse(**annual([5, 4, 3, 2])).fiscal_years == 4    # <5 años: sin dato
    f = parse(**annual([5, -4, 3, 2, 1]))
    assert (f.loss_years, f.fiscal_years) == (1, 5)


def test_loss_years_ignores_quarters_duplicates_and_stale_histories():
    quarters = [flow("2025-10-01", "2025-12-31", -999.0, "10-Q")]                              # un trimestre no es un año fiscal
    rows = annual([10, 9, 8, 7, 6])["NetIncomeLoss"] + quarters + [flow("2025-01-01", "2025-12-31", -1.0, "10-K/A", filed="2026-03-01")]
    f = parse(NetIncomeLoss=rows)
    assert (f.loss_years, f.fiscal_years) == (1, 5)                                           # la enmienda más reciente manda
    assert parse(**annual([1, 2, 3, 4, 5, 6], last_year=2022)).loss_years is None             # último año fiscal de hace años


def test_loss_years_fills_missing_years_from_the_alternative_tag():
    f = parse(NetIncomeLoss=annual([5, 4])["NetIncomeLoss"], ProfitLoss=[flow(f"{2025 - i}-01-01", f"{2025 - i}-12-31", -1.0) for i in range(2, 7)])
    assert (f.loss_years, f.fiscal_years) == (5, 7)                                           # 2 años de NetIncomeLoss + 5 de ProfitLoss
