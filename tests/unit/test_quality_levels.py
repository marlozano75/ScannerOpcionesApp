"""Grados de exigencia (flexible, estándar, estricto) de los filtros de solvencia y exenciones por sector."""
from dataclasses import replace
from datetime import date

import pytest
from pydantic import ValidationError

from scanner_opciones.config.settings import LEVELS, QualitySettings, Settings, SolvencyThresholds
from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.marketdata.financials import NO_LIMIT
from scanner_opciones.scanner.criteria import SOLVENCY_RULES, criteria_from_settings
from scanner_opciones.scanner.quality import DEFAULT_EXEMPT, is_exempt, ticker_quality_reject
from scanner_opciones.ui.web import SOLVENCY_UI, solvency_controls, quality_form

BASE = criteria_from_settings(Settings())


def info(**kw):
    kw.setdefault("sector", "Technology")
    return TickerInfo("AAPL", **kw)


def test_every_threshold_has_the_three_levels_getting_stricter():
    t = SolvencyThresholds()
    for metric in ("debt_to_equity", "interest_coverage", "cash_to_short_debt", "ocf_to_debt", "capex_to_ocf", "fcf_to_assets", "net_buyback_pct"):
        values = getattr(t, metric)
        assert tuple(values) == LEVELS
        ordered = [values[level] for level in LEVELS]
        # máximos (deuda, capex): cada grado más estricto baja el umbral; mínimos: lo sube
        assert ordered == sorted(ordered, reverse=metric in ("debt_to_equity", "capex_to_ocf")), metric


def test_the_strict_level_is_the_experts_hardest_threshold():
    t = SolvencyThresholds()
    assert t.debt_to_equity["strict"] == 0.5 and t.interest_coverage["standard"] == 3.0
    assert t.ocf_to_debt["standard"] == 0.30 and t.capex_to_ocf["standard"] == 0.35
    assert t.fcf_to_assets["strict"] == 0.12 and t.net_buyback_pct["strict"] == 2.0


def test_a_missing_level_is_a_configuration_error():
    with pytest.raises(ValidationError):
        SolvencyThresholds(debt_to_equity={"flexible": 1.5, "standard": 1.0})
    with pytest.raises(ValidationError):
        SolvencyThresholds(interest_coverage={"flexible": 2, "standard": 3, "strict": 5, "extra": 9})


def test_thresholds_can_be_overridden_from_the_configuration():
    s = Settings.model_validate({"scanner": {"quality": {"thresholds": {"debt_to_equity": {"flexible": 2, "standard": 1.2, "strict": 0.8}}}}})
    assert s.scanner.quality.thresholds.debt_to_equity["strict"] == 0.8
    assert s.scanner.quality.thresholds.interest_coverage["standard"] == 3.0       # lo demás, por defecto


def test_default_exempt_sectors_follow_the_experts_exclusions():
    assert QualitySettings().exempt_sectors == list(DEFAULT_EXEMPT)
    for sector in ("Financial", "Financial Services", "Energy", "Utilities", "Basic Materials", "Real Estate"):
        assert is_exempt(info(sector=sector))
    for sector in ("Technology", "Industrial", "Consumer Cyclical", "Communications", "Consumer, Non-cyclical", None):
        assert not is_exempt(info(sector=sector))
    assert not is_exempt(None)
    assert is_exempt(info(sector="Aerospace"), ["aero"])                 # configurable


# ---- reglas de solvencia ------------------------------------------------------------------------------------
@pytest.mark.parametrize("field,attr,limit,good,bad", [
    ("max_debt_to_equity", "debt_to_equity", 1.0, 0.9, 1.1),
    ("min_interest_coverage", "interest_coverage", 3.0, 3.5, 2.0),
    ("min_cash_to_short_debt", "cash_to_short_debt", 1.0, 1.2, 0.8),
    ("min_ocf_to_debt", "ocf_to_debt", 0.30, 0.45, 0.10),
    ("max_capex_to_ocf", "capex_to_ocf", 0.35, 0.20, 0.50),
    ("min_fcf_to_assets", "fcf_to_assets", 0.08, 0.10, 0.02),
    ("min_net_buyback_pct", "net_buyback_pct", 1.0, 2.5, -3.0),
])
def test_each_solvency_rule_compares_in_the_right_direction(field, attr, limit, good, bad):
    crit = BASE.with_filters(**{field: limit})
    assert crit.quality_active and crit.ticker_quality_active
    assert ticker_quality_reject(info(**{attr: good}), crit) is None
    assert ticker_quality_reject(info(**{attr: limit}), crit) is None                 # el umbral exacto entra
    assert ticker_quality_reject(info(**{attr: bad}), crit) is not None
    assert "(o sin dato)" in ticker_quality_reject(info(), crit)                       # sin dato no pasa
    assert ticker_quality_reject(None, crit) is not None


def test_no_limit_means_nothing_to_cover_and_passes_every_minimum():
    crit = BASE.with_filters(min_interest_coverage=5.0, min_cash_to_short_debt=2.0, min_ocf_to_debt=0.5)
    assert ticker_quality_reject(info(interest_coverage=NO_LIMIT, cash_to_short_debt=NO_LIMIT, ocf_to_debt=NO_LIMIT), crit) is None


def test_no_limit_fails_the_capex_maximum_when_operating_cash_flow_is_not_positive():
    assert ticker_quality_reject(info(capex_to_ocf=NO_LIMIT), BASE.with_filters(max_capex_to_ocf=0.6)) is not None


def test_exempt_sectors_pass_every_solvency_rule_unmeasured():
    limits = {field: 0.0 if kind == "min" else 1e-9 for field, _, kind, _ in SOLVENCY_RULES}
    limits["min_interest_coverage"] = 99.0
    crit = BASE.with_filters(**limits)
    for sector in ("Financial", "Energy", "Utilities", "Basic Materials", "Real Estate"):
        assert ticker_quality_reject(info(sector=sector, debt_to_equity=50.0, interest_coverage=0.1), crit) is None
    assert ticker_quality_reject(info(sector="Technology", debt_to_equity=50.0, interest_coverage=0.1), crit) is not None


def test_the_custom_exemption_list_is_respected():
    crit = BASE.with_filters(max_debt_to_equity=1.0)
    assert ticker_quality_reject(info(sector="Energy", debt_to_equity=5.0), crit, exempt_sectors=["financ"]) is not None
    assert ticker_quality_reject(info(sector="Energy", debt_to_equity=5.0), crit, exempt_sectors=["energy"]) is None


def test_non_solvency_filters_still_apply_to_exempt_sectors():
    crit = BASE.with_filters(require_profitable=True, max_debt_to_equity=1.0)
    assert "beneficios" in ticker_quality_reject(info(sector="Energy", eps_ttm=-1.0), crit)


# ---- opciones de la interfaz -----------------------------------------------------------------------------------
def test_the_ui_options_show_the_threshold_of_each_level():
    controls = {c["key"]: c for c in solvency_controls(QualitySettings(), quality_form(BASE))}
    assert [c["key"] for c in controls.values()] == [key for key, *_ in SOLVENCY_UI]
    assert dict(controls["q_de"]["options"]) == {"flexible": "Flexible (≤ 1.5)", "standard": "Estándar (≤ 1)", "strict": "Estricto (≤ 0.5)"}
    assert dict(controls["q_cov"]["options"])["standard"] == "Estándar (≥ 3×)"
    assert dict(controls["q_ocfd"]["options"])["standard"] == "Estándar (≥ 30 %)"
    assert dict(controls["q_capex"]["options"])["strict"] == "Estricto (≤ 20 %)"
    assert dict(controls["q_bb"]["options"])["strict"] == "Estricto (≥ 2 %)"
    assert {k for k, c in controls.items() if c["core"]} == {"q_de", "q_cov", "q_cash", "q_ocfd"}


def test_levels_translate_to_the_numbers_of_the_configuration():
    custom = QualitySettings(thresholds=SolvencyThresholds(debt_to_equity={"flexible": 3, "standard": 2, "strict": 1}))
    assert dict(solvency_controls(custom, {})[0]["options"])["strict"] == "Estricto (≤ 1)"
